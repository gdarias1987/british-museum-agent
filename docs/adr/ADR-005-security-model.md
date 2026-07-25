# ADR-005: Modelo de Seguridad — JWT, HMAC Interno y Roles

- **Fecha**: 2026-07-25
- **Estado**: Aceptado
- **Contexto**: El sistema necesita autenticar y autorizar dos tipos de actores: (1) personal del museo que puede crear incidentes operativos, y (2) visitantes que usan el chat sin autenticación. Además, la comunicación interna entre el backend y el servidor MCP debe estar autenticada sin depender de tokens de usuario ni de secrets incrustados en argumentos de herramientas. Las credenciales de personal deben almacenarse de forma segura en SQLite, y los tokens deben tener expiración. No hay registro de usuarios ni multi-tenancy: las cuentas staff se bootstrapean desde variables de entorno.
- **Decisión**: Se implementó un modelo de seguridad con tres capas:
  1. **Autenticación staff (JWT HS256)**: El endpoint `POST /api/v1/auth/login` valida credenciales contra el hash bcrypt en SQLite y devuelve un JWT firmado con `HS256` que contiene `sub` (username), `role` ("staff"), `iat` y `exp`. El JWT expira por defecto en 60 minutos (`JWT_EXPIRATION_MINUTES`). La dependencia `require_staff` valida el token en endpoints protegidos; `optional_staff` permite chat público pero valida el token si se envía.
  2. **Autenticación interna MCP (HMAC + header dedicado)**: El backend envía `MCP_INTERNAL_TOKEN` en el header `X-MCP-Internal-Token` en cada llamada MCP. El servidor MCP verifica con `hmac.compare_digest`. El token nunca aparece en argumentos de tools ni en logs. Las tools `create_incident` y `get_incident` requieren este token; `get_gallery_status` y `get_accessibility_info` son públicas internamente.
  3. **Roles (visitor/staff)**: El rol `visitor` (default) solo accede a `POST /api/v1/chat`. El rol `staff` puede además crear incidentes (`POST /api/v1/incidents`). El endpoint `/chat` sobreescribe `user_role` con el rol validado del JWT si está presente, ignorando el valor enviado por el cliente.
  
  Las contraseñas staff se almacenan únicamente como hash bcrypt (`bcrypt.hashpw` con `gensalt()`). El script `seed_db.py` migra automáticamente contraseñas en texto plano a bcrypt si existieran. La validación usa `bcrypt.checkpw` con comparación de tiempo constante.
- **Consecuencias**:
  - **Positivas**:
    - Separación clara de dominios de seguridad: JWT para humanos, HMAC para máquinas.
    - El token MCP no se filtra en argumentos de herramientas, trazas de LangSmith ni logs.
    - Las contraseñas nunca se almacenan en texto plano; bcrypt con salt previene ataques de rainbow table.
    - JWT con expiración limita la ventana de un token robado.
    - El rol `staff` no es controlable por el cliente: el backend lo asigna desde el JWT validado.
    - `optional_staff` permite que el chat funcione sin autenticación pero valide estrictamente cualquier token presente.
    - Migración automática de contraseñas legacy a bcrypt.
  - **Negativas**:
    - Sin refresh tokens: al expirar el JWT, el staff debe volver a hacer login.
    - Sin revocación de tokens: no hay blacklist ni token versioning; un JWT válido hasta su expiración no puede invalidarse antes.
    - Sin multi-tenancy: todas las cuentas staff comparten el mismo rol y permisos.
    - `hmac.compare_digest` depende de que ambos lados tengan el mismo secreto configurado en `.env`.
    - La contraseña staff se bootstrapea desde variable de entorno: si no se configura, el login staff no funciona.
    - Sin throttling ni rate limiting por defecto (mencionado como mejora futura en README).
- **Alternativas consideradas**:
  - **OAuth2 con provider externo (Google, Auth0)**: Se descartó porque añade dependencia externa innecesaria para un sistema con un solo usuario staff bootstrapeado.
  - **API keys estáticas**: Se descartó porque no permiten expiración ni distinción de roles.
  - **Sesiones con cookies**: Se descartó porque la API es stateless (FastAPI) y el cliente es Streamlit (no browser tradicional).
  - **JWT con RS256 (asimétrico)**: Sobredimensionado para un solo emisor; HS256 es más simple y no requiere gestión de par de llaves.
  - **MCP token en body de la tool**: Se descartó porque quedaría registrado en trazas y logs. Header de transporte es más seguro.
  - **Almacenamiento de passwords con hashing SHA256**: Se descartó por ser inseguro frente a ataques de fuerza bruta; bcrypt es el estándar actual.
- **Referencias**:
  - `src/british_museum_agent/api/security.py` — `create_staff_access_token`, `require_staff`, `optional_staff`, JWT_ALGORITHM
  - `src/british_museum_agent/api/main.py` — endpoints `login`, `chat`, `create_incident` con dependencias de seguridad
  - `src/british_museum_agent/adapters_mcp/server.py` — `_valid_internal_token()` con HMAC
  - `src/british_museum_agent/adapters_mcp/client.py` — `MCP_INTERNAL_TOKEN_HEADER`, `_transport_headers()`
  - `scripts/seed_db.py` — hash bcrypt y migración
  - `src/british_museum_agent/infrastructure/sqlite_repository.py` — `validate_staff_credentials` con bcrypt
  - `src/british_museum_agent/domain/models.py` — `UserRole`, `StaffIdentity`, `LoginRequest`
  - `src/british_museum_agent/config.py` — `jwt_secret`, `mcp_internal_token`, `staff_demo_password`
  - `pyproject.toml` — dependencias `bcrypt==5.0.0`, `PyJWT==2.13.0`
