import os
import io
import json
import uuid
import base64
import shutil
import yaml
import bcrypt
from datetime import datetime
import anthropic
import streamlit as st
import pdfplumber
from dotenv import load_dotenv

load_dotenv()

MODEL       = "claude-opus-4-6"
api_key     = os.getenv("ANTHROPIC_API_KEY", "")
APP_DIR     = os.path.dirname(__file__)
AUTH_FILE   = os.path.join(APP_DIR, "auth_config.yaml")

st.set_page_config(page_title="PermitFix AI", page_icon="🏗️", layout="wide")

# Hide "Press Enter to submit form" hints
st.markdown(
    "<style>[data-testid='InputInstructions'] { display: none !important; }</style>",
    unsafe_allow_html=True,
)

# ── User storage ──────────────────────────────────────────────────────────────

def load_users():
    if not os.path.isfile(AUTH_FILE):
        return {}
    with open(AUTH_FILE) as f:
        return yaml.safe_load(f) or {}

def save_users(users):
    with open(AUTH_FILE, "w") as f:
        yaml.dump(users, f, default_flow_style=False)

def hash_pw(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_pw(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed.encode())

# ── Session helpers ───────────────────────────────────────────────────────────

def is_logged_in():
    return st.session_state.get("auth_user") is not None

def do_login(email, display_name):
    st.session_state["auth_user"] = email
    st.session_state["auth_name"] = display_name

def do_logout():
    st.session_state["auth_user"] = None
    st.session_state["auth_name"] = None
    st.session_state["view"]      = "home"

# ── Auth gate ─────────────────────────────────────────────────────────────────

if not is_logged_in():
    users = load_users()

    st.title("🏗️ PermitFix AI")
    st.caption("Building permit & compliance assistant")
    st.divider()

    tab_login, tab_register = st.tabs(["Sign In", "Create Account"])

    with tab_login:
        with st.form("login_form", clear_on_submit=False):
            email_in    = st.text_input("Email")
            password_in = st.text_input("Password", type="password")
            submitted   = st.form_submit_button("Sign In", type="primary",
                                                use_container_width=True)
        if submitted:
            email_in = email_in.strip().lower()
            user = users.get(email_in)
            if user and verify_pw(password_in, user["password_hash"]):
                do_login(email_in, user["name"])
                st.rerun()
            else:
                st.error("Incorrect email or password.")

    with tab_register:
        with st.form("register_form", clear_on_submit=True):
            reg_name     = st.text_input("Full Name")
            reg_email    = st.text_input("Email")
            reg_password = st.text_input("Password", type="password")
            reg_confirm  = st.text_input("Confirm Password", type="password")
            reg_submit   = st.form_submit_button("Create Account", type="primary",
                                                  use_container_width=True)
        if reg_submit:
            reg_email = reg_email.strip().lower()
            if not reg_name.strip() or not reg_email or not reg_password:
                st.error("All fields are required.")
            elif reg_password != reg_confirm:
                st.error("Passwords do not match.")
            elif reg_email in users:
                st.error("An account with this email already exists.")
            else:
                users[reg_email] = {
                    "name": reg_name.strip(),
                    "password_hash": hash_pw(reg_password),
                }
                save_users(users)
                do_login(reg_email, reg_name.strip())
                st.rerun()

    st.stop()

# ── Logged in ─────────────────────────────────────────────────────────────────
username = st.session_state["auth_user"]
name     = st.session_state["auth_name"]

PROJECTS_DIR = os.path.join(APP_DIR, "projects", username)
IMAGE_TYPES  = ["png", "jpg", "jpeg", "webp", "gif"]
MEDIA_TYPE_MAP = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "png": "image/png",  "webp": "image/webp", "gif": "image/gif",
}
STATUSES = ["🔵 In Review", "🟡 Corrections Needed", "🟢 Approved", "⚫ On Hold"]

os.makedirs(PROJECTS_DIR, exist_ok=True)

# ── Persistence helpers ───────────────────────────────────────────────────────

def project_dir(pid):
    return os.path.join(PROJECTS_DIR, pid)

def meta_path(pid):
    return os.path.join(project_dir(pid), "meta.json")

def chat_path(pid):
    return os.path.join(project_dir(pid), "chat.json")

def files_dir(pid):
    return os.path.join(project_dir(pid), "files")

def load_all_projects():
    projects = []
    for pid in os.listdir(PROJECTS_DIR):
        mp = meta_path(pid)
        if os.path.isfile(mp):
            with open(mp) as f:
                projects.append(json.load(f))
    projects.sort(key=lambda p: p.get("modified", ""), reverse=True)
    return projects

def save_meta(meta):
    os.makedirs(project_dir(meta["id"]), exist_ok=True)
    with open(meta_path(meta["id"]), "w") as f:
        json.dump(meta, f, indent=2)

def load_meta(pid):
    with open(meta_path(pid)) as f:
        return json.load(f)

def load_chat(pid):
    cp = chat_path(pid)
    if os.path.isfile(cp):
        with open(cp) as f:
            return json.load(f)
    return []

def save_chat(pid, messages):
    with open(chat_path(pid), "w") as f:
        json.dump(messages, f, indent=2)

def load_project_files(pid):
    """Return (docs, images) lists loaded from disk."""
    fd = files_dir(pid)
    docs, images = [], []
    if not os.path.isdir(fd):
        return docs, images
    for fname in sorted(os.listdir(fd)):
        fpath = os.path.join(fd, fname)
        ext = fname.rsplit(".", 1)[-1].lower()
        raw = open(fpath, "rb").read()
        if ext == "pdf":
            try:
                text, pages = extract_pdf_text(raw)
                thumb = pdf_first_page_b64(raw)
                if text.strip():
                    docs.append({"name": fname, "text": text,
                                 "page_count": pages, "thumb_b64": thumb})
            except Exception:
                pass
        elif ext in IMAGE_TYPES:
            b64 = base64.standard_b64encode(raw).decode()
            images.append({"name": fname,
                            "media_type": MEDIA_TYPE_MAP.get(ext, "image/png"),
                            "b64": b64})
    return docs, images

def save_file_to_project(pid, fname, raw_bytes):
    fd = files_dir(pid)
    os.makedirs(fd, exist_ok=True)
    with open(os.path.join(fd, fname), "wb") as f:
        f.write(raw_bytes)

def delete_project(pid):
    shutil.rmtree(project_dir(pid), ignore_errors=True)

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")

# ── PDF / image helpers ───────────────────────────────────────────────────────

def extract_pdf_text(file_bytes: bytes):
    pages = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text.strip())
    return "\n\n".join(pages), page_count

def pdf_first_page_b64(file_bytes: bytes):
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if pdf.pages:
                img = pdf.pages[0].to_image(resolution=120)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return base64.standard_b64encode(buf.getvalue()).decode()
    except Exception:
        pass
    return None

# ── Claude helpers ────────────────────────────────────────────────────────────

def build_system_prompt(docs, focused_names=None):
    base = (
        "You are PermitFix AI, an expert assistant specializing in building permits, "
        "zoning codes, plan check corrections, and construction compliance. "
        "When analyzing drawings, check dimensions, clearances, egress, occupancy, "
        "structural elements, and accessibility. Cite code sections (IBC, CBC, OBC, "
        "NFPA, ADA, etc.) where relevant. Say so if unsure."
    )
    active = docs if not focused_names else [d for d in docs if d["name"] in focused_names]
    if not active:
        return base
    kb = "\n\n".join(f"=== {d['name']} ===\n{d['text']}" for d in active)
    return [
        {"type": "text", "text": base},
        {"type": "text", "text": f"<documents>\n{kb}\n</documents>",
         "cache_control": {"type": "ephemeral"}},
    ]

def build_api_messages(chat_messages, images, focused_names=None):
    active = images if not focused_names else [i for i in images if i["name"] in focused_names]
    if not active:
        return chat_messages
    blocks = [{"type": "image", "source": {"type": "base64",
               "media_type": i["media_type"], "data": i["b64"]}} for i in active]
    blocks.append({"type": "text", "text":
        f"The user uploaded {len(active)} drawing(s). Analyze carefully for compliance."})
    return [
        {"role": "user", "content": blocks},
        {"role": "assistant", "content":
            f"I've reviewed all {len(active)} drawing(s) in detail and am ready to analyze them."},
    ] + chat_messages

def stream_response(client, messages, system):
    full, placeholder = "", st.empty()
    with client.messages.stream(model=MODEL, max_tokens=16000,
                                system=system, messages=messages) as stream:
        for ev in stream:
            if ev.type == "content_block_delta" and ev.delta.type == "text_delta":
                full += ev.delta.text
                placeholder.markdown(full + "▌")
    placeholder.markdown(full)
    return full

# ── Session state ─────────────────────────────────────────────────────────────

def init_state():
    defaults = dict(view="home", current_pid=None, docs=[], images=[],
                    messages=[], focused=set(), prefill="", creating=False)
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

def open_project(pid):
    st.session_state.current_pid = pid
    st.session_state.view = "project"
    st.session_state.messages = load_chat(pid)
    docs, images = load_project_files(pid)
    st.session_state.docs = docs
    st.session_state.images = images
    st.session_state.focused = set()
    st.session_state.prefill = ""

def go_home():
    if st.session_state.current_pid:
        save_chat(st.session_state.current_pid, st.session_state.messages)
    st.session_state.view = "home"
    st.session_state.current_pid = None
    st.session_state.docs = []
    st.session_state.images = []
    st.session_state.messages = []
    st.session_state.focused = set()
    st.session_state.creating = False

# ═════════════════════════════════════════════════════════════════════════════
# HOME VIEW
# ═════════════════════════════════════════════════════════════════════════════

if st.session_state.view == "home":

    # Header
    col_logo, col_btn = st.columns([4, 1])
    with col_logo:
        st.title("🏗️ PermitFix AI")
        st.caption("Building permit & compliance assistant — manage all your projects in one place.")
    with col_btn:
        st.write("")
        st.write("")
        if st.button("＋ New Project", type="primary", use_container_width=True):
            st.session_state.creating = True

    st.divider()

    # New project form
    if st.session_state.creating:
        with st.container(border=True):
            st.subheader("Create New Project")
            new_name   = st.text_input("Project name", placeholder="e.g. L&J Coyle House Addition")
            new_address = st.text_input("Address (optional)", placeholder="123 Main St")
            new_status = st.selectbox("Status", STATUSES)
            new_notes  = st.text_area("Notes (optional)", height=80)
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Create", type="primary", use_container_width=True):
                    if new_name.strip():
                        pid = str(uuid.uuid4())[:8]
                        meta = dict(id=pid, name=new_name.strip(),
                                    address=new_address.strip(),
                                    status=new_status, notes=new_notes.strip(),
                                    created=now_str(), modified=now_str(),
                                    file_count=0)
                        save_meta(meta)
                        st.session_state.creating = False
                        open_project(pid)
                        st.rerun()
                    else:
                        st.error("Please enter a project name.")
            with c2:
                if st.button("Cancel", use_container_width=True):
                    st.session_state.creating = False
                    st.rerun()

    # Project grid
    projects = load_all_projects()

    if not projects:
        st.info("No projects yet. Click **＋ New Project** to get started.")
    else:
        st.subheader(f"All Projects ({len(projects)})")
        COLS = 3
        rows = [projects[i:i+COLS] for i in range(0, len(projects), COLS)]
        for row in rows:
            cols = st.columns(COLS)
            for col, p in zip(cols, row):
                with col:
                    with st.container(border=True):
                        # Thumbnail: first image found in project
                        fd = files_dir(p["id"])
                        thumb_shown = False
                        if os.path.isdir(fd):
                            for fname in sorted(os.listdir(fd)):
                                ext = fname.rsplit(".", 1)[-1].lower()
                                fpath = os.path.join(fd, fname)
                                raw = open(fpath, "rb").read()
                                if ext in IMAGE_TYPES:
                                    b64 = base64.standard_b64encode(raw).decode()
                                    mt  = MEDIA_TYPE_MAP.get(ext, "image/png")
                                    st.image(f"data:{mt};base64,{b64}",
                                             use_container_width=True)
                                    thumb_shown = True
                                    break
                                elif ext == "pdf":
                                    thumb = pdf_first_page_b64(raw)
                                    if thumb:
                                        st.image(f"data:image/png;base64,{thumb}",
                                                 use_container_width=True)
                                        thumb_shown = True
                                        break
                        if not thumb_shown:
                            st.markdown(
                                "<div style='text-align:center;font-size:56px;"
                                "padding:24px 0'>📁</div>",
                                unsafe_allow_html=True)

                        # Info
                        st.markdown(f"### {p['name']}")
                        if p.get("address"):
                            st.caption(f"📍 {p['address']}")
                        st.markdown(p.get("status", STATUSES[0]))

                        # File count
                        n_files = len(os.listdir(fd)) if os.path.isdir(fd) else 0
                        st.caption(f"📂 {n_files} file(s)  ·  Modified {p.get('modified','—')}")

                        if p.get("notes"):
                            with st.expander("Notes"):
                                st.write(p["notes"])

                        # Actions
                        a1, a2 = st.columns(2)
                        with a1:
                            if st.button("Open", key=f"open_{p['id']}",
                                         type="primary", use_container_width=True):
                                open_project(p["id"])
                                st.rerun()
                        with a2:
                            if st.button("Delete", key=f"del_{p['id']}",
                                         use_container_width=True):
                                delete_project(p["id"])
                                st.rerun()

    # API key warning at the bottom
    if not api_key or api_key == "your_api_key_here":
        st.error("⚠️  Set ANTHROPIC_API_KEY in your .env file to enable AI analysis.")

    st.divider()
    st.caption(f"Signed in as **{name}**")
    if st.button("Sign Out", use_container_width=True):
        do_logout()
        st.rerun()

# ═════════════════════════════════════════════════════════════════════════════
# PROJECT VIEW
# ═════════════════════════════════════════════════════════════════════════════

else:
    pid  = st.session_state.current_pid
    meta = load_meta(pid)

    # ── Top bar ──────────────────────────────────────────────────────────────
    col_back, col_title, col_status = st.columns([1, 4, 2])
    with col_back:
        if st.button("← All Projects"):
            go_home()
            st.rerun()
    with col_title:
        st.title(f"🏗️ {meta['name']}")
        if meta.get("address"):
            st.caption(f"📍 {meta['address']}")
    with col_status:
        new_status = st.selectbox("Status", STATUSES,
                                  index=STATUSES.index(meta.get("status", STATUSES[0])),
                                  label_visibility="collapsed")
        if new_status != meta.get("status"):
            meta["status"] = new_status
            meta["modified"] = now_str()
            save_meta(meta)

    st.divider()

    # ── Sidebar: upload ───────────────────────────────────────────────────────
    with st.sidebar:
        st.subheader(f"📂 {meta['name']}")
        st.caption(meta.get("status", ""))
        st.divider()

        st.markdown("**Upload Files**")

        pdf_files = st.file_uploader("PDFs", type="pdf",
                                      accept_multiple_files=True, key=f"pdf_{pid}")
        if pdf_files:
            current = {d["name"] for d in st.session_state.docs}
            for f in pdf_files:
                if f.name not in current:
                    with st.spinner(f"Reading {f.name}…"):
                        try:
                            raw = f.read()
                            text, pages = extract_pdf_text(raw)
                            thumb = pdf_first_page_b64(raw)
                            if text.strip():
                                save_file_to_project(pid, f.name, raw)
                                st.session_state.docs.append(
                                    {"name": f.name, "text": text,
                                     "page_count": pages, "thumb_b64": thumb})
                                meta["modified"] = now_str()
                                save_meta(meta)
                                st.success(f"✅ {f.name}")
                            else:
                                st.warning(f"⚠️ {f.name} — no extractable text")
                        except Exception as e:
                            st.error(f"❌ {f.name}: {e}")

        img_files = st.file_uploader("Drawings / Images", type=IMAGE_TYPES,
                                      accept_multiple_files=True, key=f"img_{pid}")
        if img_files:
            current = {i["name"] for i in st.session_state.images}
            for f in img_files:
                if f.name not in current:
                    with st.spinner(f"Loading {f.name}…"):
                        try:
                            ext = f.name.rsplit(".", 1)[-1].lower()
                            raw = f.read()
                            b64 = base64.standard_b64encode(raw).decode()
                            save_file_to_project(pid, f.name, raw)
                            st.session_state.images.append(
                                {"name": f.name,
                                 "media_type": MEDIA_TYPE_MAP.get(ext, "image/png"),
                                 "b64": b64})
                            meta["modified"] = now_str()
                            save_meta(meta)
                            st.success(f"✅ {f.name}")
                        except Exception as e:
                            st.error(f"❌ {f.name}: {e}")

        st.divider()
        total = len(st.session_state.docs) + len(st.session_state.images)
        focused_count = len(st.session_state.focused)
        if total:
            st.caption(f"{total} file(s) · {focused_count} selected")

        if st.button("🗑️ Clear Chat"):
            st.session_state.messages = []
            save_chat(pid, [])
            st.rerun()

        if not api_key or api_key == "your_api_key_here":
            st.error("⚠️ Set ANTHROPIC_API_KEY in .env")

        st.divider()
        st.caption(f"Signed in as **{name}**")
        if st.button("Sign Out", key="signout_sidebar", use_container_width=True):
            do_logout()
            st.rerun()

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab_dash, tab_chat = st.tabs(["📋 Plans Dashboard", "💬 Chat"])

    # ── PLANS DASHBOARD ───────────────────────────────────────────────────────
    with tab_dash:
        all_files = (
            [{"kind": "pdf",   **d} for d in st.session_state.docs] +
            [{"kind": "image", **img} for img in st.session_state.images]
        )

        if not all_files:
            st.info("No files yet — upload PDFs or images using the sidebar.")
        else:
            ca, cb, cc = st.columns([3, 1, 1])
            with ca:
                st.markdown(f"**{len(all_files)} file(s)** — select to focus the AI on specific drawings.")
            with cb:
                if st.button("✅ Select All"):
                    st.session_state.focused = {f["name"] for f in all_files}
                    st.rerun()
            with cc:
                if st.button("⬜ Deselect All"):
                    st.session_state.focused = set()
                    st.rerun()

            st.divider()
            COLS = 3
            rows = [all_files[i:i+COLS] for i in range(0, len(all_files), COLS)]
            for row in rows:
                cols = st.columns(COLS)
                for col, f in zip(cols, row):
                    with col:
                        name     = f["name"]
                        selected = name in st.session_state.focused
                        with st.container(border=True):
                            if f["kind"] == "image":
                                st.image(f"data:{f['media_type']};base64,{f['b64']}",
                                         use_container_width=True)
                            elif f.get("thumb_b64"):
                                st.image(f"data:image/png;base64,{f['thumb_b64']}",
                                         use_container_width=True)
                            else:
                                st.markdown(
                                    "<div style='text-align:center;font-size:56px;"
                                    "padding:20px'>📄</div>", unsafe_allow_html=True)

                            kind_label = "🖼️ Image" if f["kind"] == "image" else "📄 PDF"
                            pages = f.get("page_count", "")
                            st.markdown(f"**{name}**")
                            st.caption(f"{kind_label}"
                                       + (f"  ·  {pages} page(s)" if pages else ""))

                            btn_label = "✅ Selected" if selected else "Select for Analysis"
                            if st.button(btn_label, key=f"sel_{name}_{pid}"):
                                if selected:
                                    st.session_state.focused.discard(name)
                                else:
                                    st.session_state.focused.add(name)
                                st.rerun()

                            if st.button("💬 Analyze this", key=f"analyze_{name}_{pid}"):
                                st.session_state.focused  = {name}
                                st.session_state.prefill  = (
                                    f"Please do a full compliance analysis of {name}.")
                                st.session_state.messages = []
                                save_chat(pid, [])
                                st.rerun()

                            if st.button("🗑️ Remove", key=f"rm_{name}_{pid}"):
                                fpath = os.path.join(files_dir(pid), name)
                                if os.path.isfile(fpath):
                                    os.remove(fpath)
                                st.session_state.docs   = [d for d in st.session_state.docs
                                                            if d["name"] != name]
                                st.session_state.images = [i for i in st.session_state.images
                                                            if i["name"] != name]
                                st.session_state.focused.discard(name)
                                meta["modified"] = now_str()
                                save_meta(meta)
                                st.rerun()

    # ── CHAT ──────────────────────────────────────────────────────────────────
    with tab_chat:
        focused   = st.session_state.focused
        all_names = {f["name"] for f in all_files} if "all_files" in dir() else set()

        if focused and focused != all_names:
            st.info(f"🎯 Focused on: {', '.join(sorted(focused))}")
        elif not focused and (st.session_state.docs or st.session_state.images):
            st.info("ℹ️ No files selected — AI will use all uploaded files.")

        default_prompt = st.session_state.prefill
        if default_prompt:
            st.session_state.prefill = ""

        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        if prompt := st.chat_input("Ask about a permit, drawing, or code requirement…"):
            if not api_key or api_key == "your_api_key_here":
                st.error("Set your ANTHROPIC_API_KEY in the .env file first.")
                st.stop()

            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            client       = anthropic.Anthropic(api_key=api_key)
            focus_filter = focused if focused else None
            system       = build_system_prompt(st.session_state.docs, focus_filter)
            api_msgs     = build_api_messages(st.session_state.messages,
                                              st.session_state.images, focus_filter)
            with st.chat_message("assistant"):
                reply = stream_response(client, api_msgs, system)
            st.session_state.messages.append({"role": "assistant", "content": reply})
            save_chat(pid, st.session_state.messages)
            meta["modified"] = now_str()
            save_meta(meta)

        # Auto-trigger from "Analyze this" button
        if default_prompt and api_key and api_key != "your_api_key_here":
            st.session_state.messages.append({"role": "user", "content": default_prompt})
            client       = anthropic.Anthropic(api_key=api_key)
            focus_filter = st.session_state.focused if st.session_state.focused else None
            system       = build_system_prompt(st.session_state.docs, focus_filter)
            api_msgs     = build_api_messages(st.session_state.messages,
                                              st.session_state.images, focus_filter)
            with st.chat_message("user"):
                st.markdown(default_prompt)
            with st.chat_message("assistant"):
                reply = stream_response(client, api_msgs, system)
            st.session_state.messages.append({"role": "assistant", "content": reply})
            save_chat(pid, st.session_state.messages)
            meta["modified"] = now_str()
            save_meta(meta)
            st.rerun()
