"""Simulated ED resources: explicit capacity, explicit waiting lists, full logs.

Why custom allocators instead of only SimPy's built-in Resource?
    * Staff capacity changes with shifts (07:00, 15:00, 23:00).
    * A patient may accept ANY of several space types, in order of preference.
    * We want every assignment logged, so tests can prove nothing was ever
      double-booked.

Queue policy for every allocator here: NON-PREEMPTIVE PRIORITY.
    The waiting patient with the best priority gets the next free unit
    (lower tuple = served first, e.g. (ESI, time joined)). Nobody already
    being served is interrupted. Consequence: an arriving ESI-1 patient goes
    to the FRONT of the line but still waits for the next unit to free up.
    Real EDs would make room immediately (e.g., hallway care, pulling a
    clinician); this model doesn't. See docs/SIMULATION_MODEL.md.
"""

from __future__ import annotations

import heapq
import itertools
from collections import deque
from collections.abc import Hashable, Mapping, Sequence

import simpy

MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24


class ResourceIntegrityError(RuntimeError):
    """A resource invariant was violated (e.g., releasing a unit nobody holds)."""


class StepSeries:
    """A value that changes at moments in time and stays constant in between
    (e.g., "number of physicians busy"). Supports time-weighted averages."""

    def __init__(self, t0: float, v0: float) -> None:
        self.times: list[float] = [t0]
        self.values: list[float] = [v0]

    def set(self, t: float, value: float) -> None:
        if t < self.times[-1]:
            raise ResourceIntegrityError("time went backwards in a StepSeries")
        if t == self.times[-1]:
            self.values[-1] = value
        elif value != self.values[-1]:
            self.times.append(t)
            self.values.append(value)

    @property
    def current(self) -> float:
        return self.values[-1]

    def integral(self, start: float, end: float) -> float:
        """Area under the step function between start and end."""
        total = 0.0
        for i, (t, v) in enumerate(zip(self.times, self.values, strict=True)):
            seg_end = self.times[i + 1] if i + 1 < len(self.times) else end
            lo, hi = max(t, start), min(seg_end, end)
            if hi > lo:
                total += v * (hi - lo)
        return total

    def maximum(self, start: float, end: float) -> float:
        best = 0.0
        for i, (t, v) in enumerate(zip(self.times, self.values, strict=True)):
            seg_end = self.times[i + 1] if i + 1 < len(self.times) else end
            if seg_end > start and t < end:
                best = max(best, v)
        return best


class ScheduledPool:
    """Interchangeable staff (e.g., physician slots, triage nurses) whose
    capacity follows a 24-hour schedule, with a non-preemptive priority queue.

    Shift change rule: when capacity drops, clinicians going off shift FINISH
    their current patient; no new patient is started until busy < capacity.
    So `busy` can briefly exceed the new capacity, but no ASSIGNMENT is ever
    made above capacity (logged in `assignments` and checked by tests).
    """

    def __init__(self, env: simpy.Environment, name: str, capacity_by_hour: Sequence[int]) -> None:
        if len(capacity_by_hour) != HOURS_PER_DAY or any(c < 0 for c in capacity_by_hour):
            raise ValueError(f"{name}: need 24 non-negative hourly capacities")
        self.env = env
        self.name = name
        self.capacity_by_hour = tuple(int(c) for c in capacity_by_hour)
        self.busy = 0
        self._queue: list[tuple[Hashable, int, simpy.Event]] = []
        self._seq = itertools.count()
        self.busy_series = StepSeries(env.now, 0)
        self.capacity_series = StepSeries(env.now, self.capacity_at(env.now))
        self.queue_series = StepSeries(env.now, 0)
        self.assignments: list[tuple[float, int, int]] = []  # (time, busy after, capacity)
        env.process(self._follow_schedule())

    def capacity_at(self, t: float) -> int:
        return self.capacity_by_hour[int(t // MINUTES_PER_HOUR) % HOURS_PER_DAY]

    @property
    def queue_length(self) -> int:
        return len(self._queue)

    def request(self, priority: Hashable) -> simpy.Event:
        """Join the waiting list; the returned event fires when a unit is assigned."""
        event = self.env.event()
        heapq.heappush(self._queue, (priority, next(self._seq), event))
        self._dispatch()
        return event

    def release(self) -> None:
        if self.busy <= 0:
            raise ResourceIntegrityError(f"{self.name}: release with nobody busy")
        self.busy -= 1
        self._dispatch()

    def _dispatch(self) -> None:
        capacity = self.capacity_at(self.env.now)
        while self._queue and self.busy < capacity:
            _, _, event = heapq.heappop(self._queue)
            self.busy += 1
            self.assignments.append((self.env.now, self.busy, capacity))
            event.succeed()
        self.busy_series.set(self.env.now, self.busy)
        self.queue_series.set(self.env.now, len(self._queue))

    def _follow_schedule(self):  # noqa: ANN202 - SimPy process generator
        while True:
            now = self.env.now
            next_hour = (int(now // MINUTES_PER_HOUR) + 1) * MINUTES_PER_HOUR
            yield self.env.timeout(next_hour - now)
            capacity = self.capacity_at(self.env.now)
            if capacity != self.capacity_series.current:
                self.capacity_series.set(self.env.now, capacity)
                self._dispatch()  # new staff can start on waiting patients


class TreatmentSpaceManager:
    """Treatment spaces of several types; each ESI level may use certain types,
    in order of preference.

    Dispatch scans waiting patients in priority order and gives each the first
    FREE type on their list. A high-priority patient waiting for a full type
    does not block a lower-priority patient whose type is free (no
    head-of-line blocking).
    """

    def __init__(
        self,
        env: simpy.Environment,
        capacities: Mapping[str, int],
        eligibility: Mapping[int, Sequence[str]],
    ) -> None:
        self.env = env
        self.capacity = dict(capacities)
        self.free = dict(capacities)
        self.eligibility = {k: tuple(v) for k, v in eligibility.items()}
        self._waiting: list[tuple[Hashable, int, int, simpy.Event]] = []
        self._seq = itertools.count()
        self.occupied_series = {t: StepSeries(env.now, 0) for t in self.capacity}
        self.queue_series = StepSeries(env.now, 0)
        self.assignments: list[tuple[float, str, int, int]] = []  # time, type, occupied, cap

    @property
    def queue_length(self) -> int:
        return len(self._waiting)

    def occupied(self, space_type: str) -> int:
        return self.capacity[space_type] - self.free[space_type]

    def request(self, priority: Hashable, esi: int) -> simpy.Event:
        """Event value is the assigned space type."""
        event = self.env.event()
        self._waiting.append((priority, next(self._seq), esi, event))
        self._dispatch()
        return event

    def release(self, space_type: str) -> None:
        if self.free[space_type] >= self.capacity[space_type]:
            raise ResourceIntegrityError(f"release of {space_type} that nobody occupies")
        self.free[space_type] += 1
        self.occupied_series[space_type].set(self.env.now, self.occupied(space_type))
        self._dispatch()

    def _dispatch(self) -> None:
        still_waiting = []
        for entry in sorted(self._waiting, key=lambda e: (e[0], e[1])):
            _, _, esi, event = entry
            chosen = next((t for t in self.eligibility[esi] if self.free.get(t, 0) > 0), None)
            if chosen is None:
                still_waiting.append(entry)
                continue
            self.free[chosen] -= 1
            occupied = self.occupied(chosen)
            if occupied > self.capacity[chosen]:  # pragma: no cover - defensive
                raise ResourceIntegrityError(f"{chosen} double-booked")
            self.assignments.append((self.env.now, chosen, occupied, self.capacity[chosen]))
            self.occupied_series[chosen].set(self.env.now, occupied)
            event.succeed(chosen)
        self._waiting = still_waiting
        self.queue_series.set(self.env.now, len(self._waiting))


class InpatientBedQueue:
    """SIMPLIFIED inpatient bed availability for admitted ED patients (boarders).

    Beds become available to the ED at the given release times. Waiting
    boarders claim them first come, first served. A bed released while
    nobody waits is held for the ED up to `max_unclaimed`; beyond that it's
    taken by other hospital demand (counted in `lost_to_other_demand`).
    """

    def __init__(
        self,
        env: simpy.Environment,
        release_times: Sequence[float],
        *,
        initial_available: int,
        max_unclaimed: int,
    ) -> None:
        if initial_available > max_unclaimed:
            raise ValueError("initial_available cannot exceed max_unclaimed")
        self.env = env
        self.available = initial_available
        self.max_unclaimed = max_unclaimed
        self._waiting: deque[simpy.Event] = deque()
        self.released = 0
        self.claimed = 0
        self.lost_to_other_demand = 0
        self.available_series = StepSeries(env.now, initial_available)
        self.queue_series = StepSeries(env.now, 0)
        env.process(self._release_beds(sorted(release_times)))

    @property
    def queue_length(self) -> int:
        return len(self._waiting)

    def request(self) -> simpy.Event:
        event = self.env.event()
        if self.available > 0:
            self.available -= 1
            self.claimed += 1
            self.available_series.set(self.env.now, self.available)
            event.succeed()
        else:
            self._waiting.append(event)
            self.queue_series.set(self.env.now, len(self._waiting))
        return event

    def _release_beds(self, times: list[float]):  # noqa: ANN202 - SimPy process generator
        for t in times:
            if t < self.env.now:
                continue
            yield self.env.timeout(t - self.env.now)
            self.released += 1
            if self._waiting:
                self.claimed += 1
                self._waiting.popleft().succeed()
                self.queue_series.set(self.env.now, len(self._waiting))
            elif self.available < self.max_unclaimed:
                self.available += 1
                self.available_series.set(self.env.now, self.available)
            else:
                self.lost_to_other_demand += 1
