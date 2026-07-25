import os

import requests
import streamlit as st

from british_museum_agent.interfaces.streamlit.staff_session import (
    chat_role_for_session,
    clear_staff_session,
    is_staff_authenticated,
    is_manual_incident_form_visible,
    store_staff_session,
    toggle_manual_incident_form,
)

BACKEND_URL = os.getenv("STREAMLIT_BACKEND_URL", "http://localhost:8000")
BUILDING_ICON = "🏛️"

st.set_page_config(page_title="British Museum Agent", page_icon=BUILDING_ICON, layout="wide")
st.title(f"{BUILDING_ICON} British Museum Agent")
st.caption("Asistente en español para visitantes y personal, con RAG, LangGraph y herramientas MCP.")

profile = st.sidebar.radio("Modo de uso", ["Visitante", "Personal"], help="Visitante usa el chat público. Personal requiere iniciar sesión.")
if profile == "Visitante":
    st.sidebar.info("**Visitante:** chat público, sin inicio de sesión.")
else:
    st.sidebar.info("**Personal:** iniciá sesión para usar funciones de personal.")
    if is_staff_authenticated(st.session_state):
        st.sidebar.success(f"Sesión iniciada como {st.session_state['staff_username']} (personal).")
        if st.sidebar.button("Cerrar sesión"):
            clear_staff_session(st.session_state)
            st.rerun()
        st.sidebar.divider()
        st.sidebar.subheader("Operaciones del personal")
        if st.sidebar.button("Cargar incidente manualmente"):
            toggle_manual_incident_form(st.session_state)
            st.rerun()
        st.sidebar.caption("Comandos conversacionales:")
        st.sidebar.markdown(
            "- `Registrá un incidente en la sala 4 por accesibilidad, prioridad media.`\n"
            "- `Consultá el incidente #25.`\n"
            "- Cuando recibas el código de confirmación, respondé: `Confirmo <nonce>`."
        )
    else:
        with st.sidebar.form("staff-login"):
            username = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            submit_login = st.form_submit_button("Iniciar sesión")
        st.sidebar.caption("Usá STAFF_DEMO_USERNAME y STAFF_DEMO_PASSWORD de tu .env.")
        if submit_login:
            try:
                response = requests.post(f"{BACKEND_URL}/api/v1/auth/login", json={"username": username, "password": password}, timeout=20)
                if response.status_code == 401:
                    st.sidebar.error("No se pudo iniciar sesión: verificá usuario y contraseña.")
                else:
                    response.raise_for_status()
                    data = response.json()
                    store_staff_session(st.session_state, access_token=data.get("access_token", ""), username=username, role=data.get("role", ""))
                    st.rerun()
            except (requests.RequestException, ValueError):
                st.sidebar.error("No se pudo iniciar sesión. Verificá que el backend esté disponible.")

authenticated_staff = is_staff_authenticated(st.session_state)
location_hint = st.sidebar.text_input("Ubicación opcional", placeholder="Sala 4, Egipto, Medio Oriente...")
message = st.text_area("Consultale al agente", value="¿Qué es la Piedra de Rosetta y por qué es importante?", height=120)

if st.button("Enviar", type="primary"):
    payload = {"message": message, "user_role": chat_role_for_session(st.session_state, profile), "session_id": "streamlit-demo", "language": "es", "location_hint": location_hint or None, "metadata": {"client": "streamlit"}}
    try:
        headers = ({"Authorization": f"Bearer {st.session_state['access_token']}"}
                   if authenticated_staff else {})
        response = requests.post(f"{BACKEND_URL}/api/v1/chat", json=payload, headers=headers, timeout=120)
        response.raise_for_status()
        data = response.json()
        st.subheader("Respuesta")
        st.write(data["answer"])
        st.metric("Confianza", f"{data['confidence']:.2f}")
        st.caption(f"Trace ID: {data['trace_id']}")
        st.subheader("Fuentes")
        if not data["sources"]:
            st.info("La respuesta no utilizó fuentes porque no hubo evidencia suficiente.")
        for source in data["sources"]:
            st.markdown(f"**{source['title']}** — relevancia {source['score']:.2f}")
            st.caption(source.get("url") or source["source"])
            st.write(source["excerpt"])
    except requests.RequestException as exc:
        st.error(f"No se pudo consultar el backend: {type(exc).__name__}")
        st.info(f"Backend esperado en {BACKEND_URL}")

if profile == "Personal" and authenticated_staff and is_manual_incident_form_visible(st.session_state):
    st.divider()
    st.subheader("Registrar incidente")
    st.caption("Esta acción se registra con tu sesión de personal.")
    with st.form("incident-form", clear_on_submit=True):
        gallery_id = st.text_input("ID de sala", value="room-4")
        category = st.text_input("Categoría", placeholder="Ej.: accesibilidad, señalización")
        description = st.text_area("Descripción", placeholder="Describí el incidente con claridad.")
        priority = st.selectbox("Prioridad", ["low", "medium", "high"], format_func={"low": "Baja", "medium": "Media", "high": "Alta"}.get)
        submit_incident = st.form_submit_button("Registrar incidente", type="primary")
    if submit_incident:
        try:
            response = requests.post(f"{BACKEND_URL}/api/v1/incidents", json={"gallery_id": gallery_id, "category": category, "description": description, "priority": priority}, headers={"Authorization": f"Bearer {st.session_state['access_token']}"}, timeout=20)
            if response.status_code in (401, 403):
                clear_staff_session(st.session_state)
                st.error("Tu sesión venció o no es válida. Iniciá sesión nuevamente.")
            elif response.ok:
                st.success(f"Incidente #{response.json()['id']} registrado correctamente.")
            else:
                st.error("No se pudo registrar el incidente. Revisá los datos e intentá nuevamente.")
        except requests.RequestException:
            st.error("No se pudo registrar el incidente: el backend no está disponible.")
