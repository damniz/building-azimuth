import streamlit as st

from azimuth.api import exception_handlers, routes

app = st.App(
    "streamlit_app.py",
    routes=routes,
    exception_handlers=exception_handlers,
)
