import pytest

from british_museum_agent.interfaces.streamlit.staff_session import (
    chat_role_for_session,
    clear_staff_session,
    is_manual_incident_form_visible,
    is_staff_authenticated,
    store_staff_session,
    toggle_manual_incident_form,
)


def test_unauthenticated_session_is_always_a_visitor():
    state = {"staff_role": "staff"}
    assert is_staff_authenticated(state) is False
    assert chat_role_for_session(state) == "visitor"


def test_staff_session_requires_token_and_can_be_cleared():
    state = {}
    store_staff_session(state, access_token="jwt-token", username="staff@example.com", role="staff")
    assert is_staff_authenticated(state) is True
    assert chat_role_for_session(state) == "staff"
    assert chat_role_for_session(state, "Visitante") == "visitor"
    clear_staff_session(state)
    assert state == {}


def test_staff_session_rejects_non_staff_auth_response():
    with pytest.raises(ValueError):
        store_staff_session({}, access_token="jwt-token", username="user", role="visitor")


def test_manual_incident_form_is_hidden_until_staff_opens_it_and_clears_on_logout():
    state = {}
    assert is_manual_incident_form_visible(state) is False
    assert toggle_manual_incident_form(state) is True
    assert is_manual_incident_form_visible(state) is True
    assert toggle_manual_incident_form(state) is False

    toggle_manual_incident_form(state)
    clear_staff_session(state)
    assert is_manual_incident_form_visible(state) is False