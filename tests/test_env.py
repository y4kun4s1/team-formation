import inspect

import psycopg
import streamlit as st


def test_streamlit_supports_container_key_and_fragment_run_every():
    assert "key" in inspect.signature(st.container).parameters
    assert "run_every" in inspect.signature(st.fragment).parameters


def test_postgres_reachable(db_url):
    with psycopg.connect(db_url) as c:
        assert c.execute("select 1").fetchone() == (1,)
