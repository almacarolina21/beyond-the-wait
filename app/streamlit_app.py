"""Beyond the Wait — Bronx ED Access Lab dashboard.

Run from the repository root:
    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import streamlit as st

from beyond_the_wait.dashboard.ui import apply_style

st.set_page_config(
    page_title="Beyond the Wait · ED Access Lab",
    page_icon=":material/monitor_heart:",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_style()

navigation = st.navigation(
    {
        "Start here": [
            st.Page("views/overview.py", title="Overview", icon=":material/home:", default=True),
        ],
        "Real world · PUBLIC": [
            st.Page("views/public_data.py", title="Bronx Public Data", icon=":material/public:"),
        ],
        "Fictional model · SIMULATED": [
            st.Page(
                "views/patient_flow.py", title="Fictional Patient Flow", icon=":material/timeline:"
            ),
            st.Page("views/what_if.py", title="What-If Lab", icon=":material/science:"),
            st.Page(
                "views/methods.py",
                title="Sensitivity, Validation & Methods",
                icon=":material/fact_check:",
            ),
        ],
    }
)
with st.sidebar:
    st.caption(
        "Fictional, educational decision-support demo. Not clinically validated. "
        "Real public data and simulated results are never mixed on one chart."
    )
navigation.run()
