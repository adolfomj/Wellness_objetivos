#!/usr/bin/env python3
"""
Objetivos del Equipo - Servidor
================================
Servidor sin dependencias externas (solo librería estándar de Python 3.9+).

- Sirve la web (carpeta /public).
- Expone una API REST en /api/* con persistencia en SQLite (carpeta /data).
- Login solo con nombre (sin contraseña) -> token de sesión.
- Roles: admin (puede eliminar definitivamente), lider y colaborador
  (estos últimos solo ocultan/desactivan para no perder datos).
- Todo cambio queda registrado en la tabla `activity`.

Uso:  python server.py            (puerto 8000 por defecto)
      set PORT=8080 && python server.py
"""
import json
import os
import re
import secrets
import socket
import sqlite3
import traceback
from datetime import date, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
DB_PATH = os.environ.get("OBJ_DB", os.path.join(BASE_DIR, "data", "objetivos.db"))
PORT = int(os.environ.get("PORT", "8000"))

ROLES = ("admin", "lider", "colaborador")
STATUSES = ("sin_iniciar", "en_progreso", "terminada", "faltante")
PRIORITIES = ("baja", "media", "alta")
STATUS_LABEL = {
    "sin_iniciar": "Sin iniciar",
    "en_progreso": "En progreso",
    "terminada": "Terminada",
    "faltante": "Faltante",
}
ROLE_LABEL = {"admin": "Administrador", "lider": "Líder", "colaborador": "Colaborador"}
FIELD_LABEL = {
    "title": "título", "description": "descripción", "objective_id": "objetivo",
    "estimated_minutes": "tiempo estimado", "priority": "prioridad", "due_at": "fecha límite",
    "help_note": "nota de ayuda", "start_date": "fecha de inicio", "end_date": "fecha de fin",
    "metric_target": "meta", "metric_unit": "unidad", "due_time": "hora límite",
    "weekdays": "días", "assignee_id": "responsable", "name": "nombre",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  role TEXT NOT NULL DEFAULT 'colaborador' CHECK (role IN ('admin','lider','colaborador')),
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  last_login TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS objectives (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  start_date TEXT,
  end_date TEXT,
  metric_target REAL,
  metric_current REAL NOT NULL DEFAULT 0,
  metric_unit TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  hidden_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  hidden_at TEXT,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS routines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  objective_id INTEGER REFERENCES objectives(id) ON DELETE SET NULL,
  assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  estimated_minutes INTEGER NOT NULL DEFAULT 30,
  due_time TEXT NOT NULL DEFAULT '18:00',
  priority TEXT NOT NULL DEFAULT 'media' CHECK (priority IN ('baja','media','alta')),
  weekdays TEXT NOT NULL DEFAULT '1,2,3,4,5,6,7',
  active INTEGER NOT NULL DEFAULT 1,
  hidden_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  hidden_at TEXT,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  objective_id INTEGER REFERENCES objectives(id) ON DELETE SET NULL,
  routine_id INTEGER REFERENCES routines(id) ON DELETE SET NULL,
  routine_date TEXT,
  assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  estimated_minutes INTEGER NOT NULL DEFAULT 30,
  status TEXT NOT NULL DEFAULT 'sin_iniciar'
         CHECK (status IN ('sin_iniciar','en_progreso','terminada','faltante')),
  priority TEXT NOT NULL DEFAULT 'media' CHECK (priority IN ('baja','media','alta')),
  due_at TEXT,
  needs_help INTEGER NOT NULL DEFAULT 0,
  help_note TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  hidden_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  hidden_at TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_routine_day
  ON tasks(routine_id, routine_date) WHERE routine_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_tasks_assignee ON tasks(assignee_id);
CREATE INDEX IF NOT EXISTS ix_tasks_objective ON tasks(objective_id);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  user_name TEXT NOT NULL,
  action TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id INTEGER,
  summary TEXT NOT NULL,
  details TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_activity_created ON activity(created_at);
"""


# --------------------------------------------------------------------------- utilidades
class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status
        self.msg = msg


def now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = db()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_row(conn, table, rid):
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (rid,)).fetchone()
    if row is None:
        raise ApiError(404, "No se encontró el registro solicitado.")
    return dict(row)


def insert_row(conn, table, data):
    cols = ", ".join(data)
    marks = ", ".join("?" * len(data))
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(data.values()))
    return cur.lastrowid


def update_row(conn, table, rid, data):
    sets = ", ".join(f"{k} = ?" for k in data)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*data.values(), rid))


def user_name(conn, uid):
    if uid is None:
        return "nadie"
    row = conn.execute("SELECT name FROM users WHERE id = ?", (uid,)).fetchone()
    return row["name"] if row else f"#{uid}"


def log(conn, user, action, entity, entity_id, summary, details=None):
    conn.execute(
        "INSERT INTO activity (user_id, user_name, action, entity, entity_id, summary, details, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            user["id"] if user else None,
            user["name"] if user else "Sistema",
            action, entity, entity_id, summary,
            json.dumps(details, ensure_ascii=False, default=str) if details else None,
            now(),
        ),
    )


def is_lead(user):
    return user["role"] in ("admin", "lider")


def require_lead(user, msg="Solo un líder o administrador puede hacer esto."):
    if not is_lead(user):
        raise ApiError(403, msg)


def require_admin(user):
    if user["role"] != "admin":
        raise ApiError(403, "Solo un administrador puede eliminar definitivamente. Puedes ocultarlo en su lugar.")


# --------------------------------------------------------------------------- validadores
def v_text(maxlen, required=False):
    def f(v):
        v = "" if v is None else str(v).strip()[:maxlen]
        if required and not v:
            raise ApiError(400, "Hay un campo obligatorio vacío.")
        return v
    return f


def v_choice(options):
    def f(v):
        if v not in options:
            raise ApiError(400, f"Valor no válido: {v}")
        return v
    return f


def v_int(minv=0, maxv=100000, nullable=True):
    def f(v):
        if v in (None, "", "null"):
            if nullable:
                return None
            raise ApiError(400, "Falta un número obligatorio.")
        try:
            n = int(float(v))
        except (TypeError, ValueError):
            raise ApiError(400, f"Número no válido: {v}")
        if n < minv or n > maxv:
            raise ApiError(400, f"Número fuera de rango: {v}")
        return n
    return f


def v_float(nullable=True):
    def f(v):
        if v in (None, "", "null"):
            if nullable:
                return None
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            raise ApiError(400, f"Número no válido: {v}")
    return f


def v_regex(pattern, msg, nullable=True):
    rx = re.compile(pattern)

    def f(v):
        if v in (None, ""):
            if nullable:
                return None
            raise ApiError(400, msg)
        v = str(v).strip()
        if not rx.match(v):
            raise ApiError(400, msg)
        return v
    return f


def v_bool(v):
    return 1 if v in (True, 1, "1", "true", "on", "si", "sí") else 0


v_datetime = v_regex(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", "Fecha y hora no válidas (AAAA-MM-DDTHH:MM).")
v_date = v_regex(r"^\d{4}-\d{2}-\d{2}$", "Fecha no válida (AAAA-MM-DD).")
v_time = v_regex(r"^\d{2}:\d{2}$", "Hora no válida (HH:MM).", nullable=False)
v_weekdays = v_regex(r"^[1-7](,[1-7])*$", "Selecciona al menos un día.", nullable=False)

TASK_FIELDS = {
    "title": v_text(150, True), "description": v_text(2000),
    "objective_id": v_int(1, 10**9), "assignee_id": v_int(1, 10**9),
    "estimated_minutes": v_int(0, 100000, nullable=False),
    "status": v_choice(STATUSES), "priority": v_choice(PRIORITIES),
    "due_at": v_datetime, "needs_help": v_bool, "help_note": v_text(500), "active": v_bool,
}
OBJECTIVE_FIELDS = {
    "title": v_text(150, True), "description": v_text(2000),
    "start_date": v_date, "end_date": v_date,
    "metric_target": v_float(), "metric_current": v_float(nullable=False),
    "metric_unit": v_text(30), "active": v_bool,
}
ROUTINE_FIELDS = {
    "title": v_text(150, True), "description": v_text(2000),
    "objective_id": v_int(1, 10**9), "assignee_id": v_int(1, 10**9),
    "estimated_minutes": v_int(0, 100000, nullable=False), "due_time": v_time,
    "priority": v_choice(PRIORITIES), "weekdays": v_weekdays, "active": v_bool,
}
USER_FIELDS = {"name": v_text(40, True), "role": v_choice(ROLES), "active": v_bool}


def parse_fields(spec, body):
    if not isinstance(body, dict):
        raise ApiError(400, "Cuerpo de la petición no válido.")
    return {k: spec[k](body[k]) for k in body if k in spec}


def only_changes(row, data):
    return {k: v for k, v in data.items() if row.get(k) != v}


def check_refs(conn, data):
    if data.get("objective_id") is not None:
        if not conn.execute("SELECT 1 FROM objectives WHERE id = ?", (data["objective_id"],)).fetchone():
            raise ApiError(400, "El objetivo seleccionado no existe.")
    if data.get("assignee_id") is not None:
        r = conn.execute("SELECT active FROM users WHERE id = ?", (data["assignee_id"],)).fetchone()
        if not r or not r["active"]:
            raise ApiError(400, "El responsable seleccionado no existe o está desactivado.")


def apply_hide(user, row, data):
    """Completa los campos de auditoría al ocultar/restaurar."""
    if "active" in data:
        if data["active"] == 0:
            data["hidden_by"] = user["id"]
            data["hidden_at"] = now()
        else:
            data["hidden_by"] = None
            data["hidden_at"] = None


def describe_changes(conn, changes):
    parts = []
    for k, (old, new) in changes.items():
        if k == "status":
            parts.append(f'cambió el estado de "{STATUS_LABEL.get(old, old)}" a "{STATUS_LABEL.get(new, new)}"')
        elif k == "assignee_id":
            parts.append(f"la asignó a {user_name(conn, new)}" if new else "la dejó sin asignar")
        elif k == "needs_help":
            parts.append("pidió ayuda" if new else "quitó la solicitud de ayuda")
        elif k == "active":
            parts.append("la ocultó" if not new else "la restauró")
        elif k == "metric_current":
            parts.append(f"actualizó el avance de {old:g} a {new:g}")
        elif k == "role":
            parts.append(f"cambió el rol a {ROLE_LABEL.get(new, new)}")
        elif k in FIELD_LABEL:
            parts.append(f"editó {FIELD_LABEL[k]}")
    return ", ".join(parts) or "editó"


# --------------------------------------------------------------------------- auth
def clean_name(name):
    name = " ".join(str(name or "").split())
    if len(name) < 2 or len(name) > 40:
        raise ApiError(400, "Escribe tu nombre (entre 2 y 40 caracteres).")
    return name


def login(conn, body):
    name = clean_name(body.get("name"))
    row = conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
    ts = now()
    if row is None:
        first = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 0
        role = "admin" if first else "colaborador"
        uid = insert_row(conn, "users", {"name": name, "role": role, "created_at": ts})
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        log(conn, dict(row), "crear", "usuario", uid,
            f"Se registró en el equipo como {ROLE_LABEL[role]}")
    elif not row["active"]:
        raise ApiError(403, "Tu usuario está desactivado. Pide a un líder o administrador que lo reactive.")
    token = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)", (token, row["id"], ts))
    conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (ts, row["id"]))
    user = dict(row)
    user["last_login"] = ts
    log(conn, user, "login", "usuario", user["id"], "Entró a la aplicación")
    return {"token": token, "user": user}


def authenticate(conn, token):
    if not token:
        raise ApiError(401, "Debes ingresar con tu nombre.")
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?", (token,)
    ).fetchone()
    if row is None or not row["active"]:
        raise ApiError(401, "Tu sesión expiró. Vuelve a ingresar con tu nombre.")
    return dict(row)


# --------------------------------------------------------------------------- rutinas diarias
def generate_routine_tasks(conn):
    """Crea (una sola vez por día) las tareas de las rutinas diarias activas."""
    today = date.today()
    ds = today.isoformat()
    wd = str(today.isoweekday())
    hhmm = datetime.now().strftime("%H:%M")
    routines = conn.execute("SELECT * FROM routines WHERE active = 1").fetchall()
    for r in routines:
        if wd not in r["weekdays"].split(","):
            continue
        # Si la rutina se creó hoy después de su hora límite, empieza mañana.
        if r["created_at"][:10] == ds and r["due_time"] <= hhmm:
            continue
        assignee = r["assignee_id"]
        if assignee is not None:
            a = conn.execute("SELECT active FROM users WHERE id = ?", (assignee,)).fetchone()
            if not a or not a["active"]:
                assignee = None
        ts = now()
        cur = conn.execute(
            "INSERT OR IGNORE INTO tasks (title, description, objective_id, routine_id, routine_date,"
            " assignee_id, estimated_minutes, priority, due_at, created_by, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["title"], r["description"], r["objective_id"], r["id"], ds, assignee,
             r["estimated_minutes"], r["priority"], f"{ds}T{r['due_time']}", r["created_by"], ts, ts),
        )
        if cur.rowcount:
            log(conn, None, "crear", "tarea", cur.lastrowid,
                f'Generó la tarea diaria "{r["title"]}" para hoy')


# --------------------------------------------------------------------------- lecturas
def bootstrap(conn, user):
    generate_routine_tasks(conn)
    cutoff = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S")
    users = conn.execute(
        "SELECT id, name, role, active, created_at, last_login FROM users ORDER BY name COLLATE NOCASE"
    ).fetchall()
    objectives = conn.execute("SELECT * FROM objectives ORDER BY active DESC, end_date").fetchall()
    tasks = conn.execute(
        "SELECT * FROM tasks WHERE NOT (status = 'terminada' AND active = 1 AND completed_at < ?)"
        " ORDER BY due_at IS NULL, due_at", (cutoff,)
    ).fetchall()
    routines = conn.execute("SELECT * FROM routines ORDER BY due_time").fetchall()
    return {
        "me": user,
        "users": [dict(r) for r in users],
        "objectives": [dict(r) for r in objectives],
        "tasks": [dict(r) for r in tasks],
        "routines": [dict(r) for r in routines],
        "server_time": now(),
    }


def list_activity(conn, query):
    try:
        limit = max(1, min(1000, int(query.get("limit", 200))))
    except ValueError:
        limit = 200
    sql = "SELECT * FROM activity"
    args, where = [], []
    if query.get("user_id"):
        where.append("user_id = ?")
        args.append(query["user_id"])
    if query.get("entity"):
        where.append("entity = ?")
        args.append(query["entity"])
    if query.get("entity_id"):
        where.append("entity_id = ?")
        args.append(query["entity_id"])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


# --------------------------------------------------------------------------- tareas
def create_task(conn, user, body):
    data = parse_fields(TASK_FIELDS, body)
    data.pop("active", None)
    if not data.get("title"):
        raise ApiError(400, "El título de la tarea es obligatorio.")
    check_refs(conn, data)
    if user["role"] == "colaborador" and data.get("assignee_id") not in (None, user["id"]):
        raise ApiError(403, "Como colaborador solo puedes asignarte tareas a ti o dejarlas libres.")
    ts = now()
    status = data.get("status", "sin_iniciar")
    if status in ("en_progreso", "terminada"):
        data["started_at"] = ts
    if status == "terminada":
        data["completed_at"] = ts
        data["needs_help"] = 0
    data.update(created_by=user["id"], created_at=ts, updated_at=ts)
    tid = insert_row(conn, "tasks", data)
    extra = f" y la asignó a {user_name(conn, data['assignee_id'])}" if data.get("assignee_id") else ""
    log(conn, user, "crear", "tarea", tid, f'Creó la tarea "{data["title"]}"{extra}')
    return get_row(conn, "tasks", tid)


def update_task(conn, user, tid, body):
    row = get_row(conn, "tasks", tid)
    data = only_changes(row, parse_fields(TASK_FIELDS, body))
    if not data:
        return row
    if user["role"] == "colaborador":
        is_assignee = row["assignee_id"] == user["id"]
        is_creator = row["created_by"] == user["id"]
        if not (is_assignee or is_creator):
            raise ApiError(403, "Solo puedes modificar tareas asignadas a ti o creadas por ti. Pide a un líder que te la asigne.")
        if not is_creator and set(data) - {"status", "needs_help", "help_note", "assignee_id", "active"}:
            raise ApiError(403, "Solo quien creó la tarea o un líder puede editar esos datos.")
        if "assignee_id" in data and data["assignee_id"] not in (None, user["id"]):
            raise ApiError(403, "Como colaborador solo puedes asignarte la tarea a ti o liberarla.")
        if data.get("active") == 1 and row["hidden_by"] != user["id"]:
            raise ApiError(403, "Solo un líder o quien la ocultó puede restaurarla.")
    check_refs(conn, data)
    changes = {k: (row[k], v) for k, v in data.items()}
    ts = now()
    if "status" in data:
        if data["status"] in ("en_progreso", "terminada") and not row["started_at"]:
            data["started_at"] = ts
        if data["status"] == "terminada":
            data["completed_at"] = ts
            data["needs_help"] = 0
        elif row["status"] == "terminada":
            data["completed_at"] = None
    apply_hide(user, row, data)
    data["updated_at"] = ts
    update_row(conn, "tasks", tid, data)
    action = "ocultar" if data.get("active") == 0 else "restaurar" if data.get("active") == 1 else "editar"
    log(conn, user, action, "tarea", tid,
        f'Tarea "{row["title"]}": {describe_changes(conn, changes)}',
        {k: {"antes": o, "despues": n} for k, (o, n) in changes.items()})
    return get_row(conn, "tasks", tid)


def take_task(conn, user, tid):
    row = get_row(conn, "tasks", tid)
    if not row["active"]:
        raise ApiError(400, "Esta tarea está oculta.")
    if row["assignee_id"] == user["id"]:
        return row
    if row["assignee_id"] is not None:
        raise ApiError(409, f"Esta tarea ya la tomó {user_name(conn, row['assignee_id'])}.")
    conn.execute("UPDATE tasks SET assignee_id = ?, updated_at = ? WHERE id = ?", (user["id"], now(), tid))
    log(conn, user, "tomar", "tarea", tid, f'Tomó la tarea "{row["title"]}"')
    return get_row(conn, "tasks", tid)


# --------------------------------------------------------------------------- objetivos
def create_objective(conn, user, body):
    require_lead(user, "Solo un líder o administrador puede crear objetivos.")
    data = parse_fields(OBJECTIVE_FIELDS, body)
    data.pop("active", None)
    if not data.get("title"):
        raise ApiError(400, "El título del objetivo es obligatorio.")
    ts = now()
    data.update(created_by=user["id"], created_at=ts, updated_at=ts)
    oid = insert_row(conn, "objectives", data)
    log(conn, user, "crear", "objetivo", oid, f'Creó el objetivo "{data["title"]}"')
    return get_row(conn, "objectives", oid)


def update_objective(conn, user, oid, body):
    row = get_row(conn, "objectives", oid)
    data = only_changes(row, parse_fields(OBJECTIVE_FIELDS, body))
    if not data:
        return row
    if not is_lead(user) and set(data) - {"metric_current"}:
        raise ApiError(403, "Solo un líder puede editar objetivos. Tú puedes actualizar el avance de la meta.")
    changes = {k: (row[k], v) for k, v in data.items()}
    apply_hide(user, row, data)
    data["updated_at"] = now()
    update_row(conn, "objectives", oid, data)
    action = "ocultar" if data.get("active") == 0 else "restaurar" if data.get("active") == 1 else "editar"
    summary = describe_changes(conn, changes).replace("la ocultó", "lo ocultó").replace("la restauró", "lo restauró")
    log(conn, user, action, "objetivo", oid, f'Objetivo "{row["title"]}": {summary}',
        {k: {"antes": o, "despues": n} for k, (o, n) in changes.items()})
    return get_row(conn, "objectives", oid)


# --------------------------------------------------------------------------- rutinas
def create_routine(conn, user, body):
    require_lead(user, "Solo un líder o administrador puede crear rutinas diarias.")
    data = parse_fields(ROUTINE_FIELDS, body)
    data.pop("active", None)
    if not data.get("title"):
        raise ApiError(400, "El título de la rutina es obligatorio.")
    check_refs(conn, data)
    ts = now()
    data.update(created_by=user["id"], created_at=ts, updated_at=ts)
    rid = insert_row(conn, "routines", data)
    log(conn, user, "crear", "rutina", rid, f'Creó la rutina diaria "{data["title"]}"')
    return get_row(conn, "routines", rid)


def update_routine(conn, user, rid, body):
    require_lead(user, "Solo un líder o administrador puede editar rutinas diarias.")
    row = get_row(conn, "routines", rid)
    data = only_changes(row, parse_fields(ROUTINE_FIELDS, body))
    if not data:
        return row
    check_refs(conn, data)
    changes = {k: (row[k], v) for k, v in data.items()}
    apply_hide(user, row, data)
    data["updated_at"] = now()
    update_row(conn, "routines", rid, data)
    action = "ocultar" if data.get("active") == 0 else "restaurar" if data.get("active") == 1 else "editar"
    log(conn, user, action, "rutina", rid, f'Rutina "{row["title"]}": {describe_changes(conn, changes)}')
    return get_row(conn, "routines", rid)


# --------------------------------------------------------------------------- usuarios
def update_user(conn, user, uid, body):
    target = get_row(conn, "users", uid)
    data = parse_fields(USER_FIELDS, body)
    if "name" in data:
        data["name"] = clean_name(data["name"])
    data = only_changes(target, data)
    if not data:
        return target
    if user["role"] == "lider":
        if set(data) - {"active"} or target["role"] != "colaborador":
            raise ApiError(403, "Un líder solo puede activar o desactivar colaboradores.")
    elif user["role"] != "admin":
        raise ApiError(403, "No tienes permisos para gestionar usuarios.")
    losing_admin = target["role"] == "admin" and (
        data.get("role", "admin") != "admin" or data.get("active", 1) == 0)
    if losing_admin:
        others = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND active = 1 AND id != ?", (uid,)
        ).fetchone()["c"]
        if others == 0:
            raise ApiError(400, "Debe quedar al menos un administrador activo.")
    changes = {k: (target[k], v) for k, v in data.items()}
    try:
        update_row(conn, "users", uid, data)
    except sqlite3.IntegrityError:
        raise ApiError(409, "Ya existe otra persona con ese nombre.")
    if data.get("active") == 0:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    summary = describe_changes(conn, changes).replace("la ocultó", "lo desactivó").replace("la restauró", "lo reactivó")
    log(conn, user, "editar", "usuario", uid, f"Usuario {target['name']}: {summary}")
    return get_row(conn, "users", uid)


# --------------------------------------------------------------------------- eliminación (solo admin)
ENTITY = {"tasks": "tarea", "objectives": "objetivo", "routines": "rutina", "users": "usuario"}


def delete_row(conn, user, table, rid):
    require_admin(user)
    row = get_row(conn, table, rid)
    if table == "users" and rid == user["id"]:
        raise ApiError(400, "No puedes eliminar tu propio usuario.")
    label = row.get("title") or row.get("name")
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (rid,))
    log(conn, user, "eliminar", ENTITY[table], rid,
        f'Eliminó definitivamente {ENTITY[table]} "{label}"', row)
    return {"ok": True}


# --------------------------------------------------------------------------- router
def dispatch(conn, method, parts, query, body, token):
    if method == "POST" and parts == ["login"]:
        return login(conn, body)
    if method == "GET" and parts == ["public", "users"]:
        rows = conn.execute("SELECT id, name FROM users WHERE active = 1 ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]

    user = authenticate(conn, token)

    if method == "POST" and parts == ["logout"]:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return {"ok": True}
    if method == "GET" and parts == ["bootstrap"]:
        return bootstrap(conn, user)
    if method == "GET" and parts == ["activity"]:
        return list_activity(conn, query)

    if not parts:
        raise ApiError(404, "Ruta no encontrada.")
    resource, rid, sub = parts[0], None, None
    if len(parts) >= 2:
        try:
            rid = int(parts[1])
        except ValueError:
            raise ApiError(404, "Ruta no encontrada.")
    if len(parts) >= 3:
        sub = parts[2]

    if resource == "tasks":
        if method == "POST" and rid is None:
            return create_task(conn, user, body)
        if method == "POST" and rid and sub == "take":
            return take_task(conn, user, rid)
        if method == "PATCH" and rid and not sub:
            return update_task(conn, user, rid, body)
        if method == "DELETE" and rid and not sub:
            return delete_row(conn, user, "tasks", rid)
    elif resource == "objectives":
        if method == "POST" and rid is None:
            return create_objective(conn, user, body)
        if method == "PATCH" and rid:
            return update_objective(conn, user, rid, body)
        if method == "DELETE" and rid:
            return delete_row(conn, user, "objectives", rid)
    elif resource == "routines":
        if method == "POST" and rid is None:
            return create_routine(conn, user, body)
        if method == "PATCH" and rid:
            return update_routine(conn, user, rid, body)
        if method == "DELETE" and rid:
            return delete_row(conn, user, "routines", rid)
    elif resource == "users":
        if method == "PATCH" and rid:
            return update_user(conn, user, rid, body)
        if method == "DELETE" and rid:
            return delete_row(conn, user, "users", rid)
    raise ApiError(404, "Ruta no encontrada.")


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".json": "application/json",
        ".webmanifest": "application/manifest+json",
        ".svg": "image/svg+xml",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PUBLIC_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        # Silencia las consultas periódicas; muestra cambios y errores.
        msg = fmt % args
        if '"GET ' in msg and " 200 " in msg or " 304 " in msg:
            return
        print(f"[{datetime.now():%H:%M:%S}] {msg}")

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._api("GET")
        else:
            super().do_GET()

    def do_POST(self):
        self._api("POST")

    def do_PATCH(self):
        self._api("PATCH")

    def do_DELETE(self):
        self._api("DELETE")

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if length > 1_000_000:
            raise ApiError(413, "Petición demasiado grande.")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "JSON no válido.")

    def _send_json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _api(self, method):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return self._send_json(404, {"error": "Ruta no encontrada."})
        parts = [p for p in parsed.path.split("/") if p][1:]
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        conn = db()
        try:
            body = self._read_body() if method in ("POST", "PATCH") else {}
            result = dispatch(conn, method, parts, query, body, self.headers.get("X-Token", ""))
            conn.commit()
            self._send_json(200, result)
        except ApiError as e:
            conn.rollback()
            self._send_json(e.status, {"error": e.msg})
        except Exception:  # noqa: BLE001
            conn.rollback()
            traceback.print_exc()
            self._send_json(500, {"error": "Error interno del servidor."})
        finally:
            conn.close()


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main():
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("=" * 60)
    print(" Objetivos del Equipo - servidor iniciado")
    print(f"  En este equipo:      http://localhost:{PORT}")
    print(f"  Desde el celular:    http://{lan_ip()}:{PORT}  (misma red Wi-Fi)")
    print(f"  Base de datos:       {DB_PATH}")
    print("  Ctrl+C para detener")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
