"""
LlamaDesktop — Tkinter UI para Ollama
Inicie com: python app.py
"""
import json
import os
import re
import winsound
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List

import ollama_client
import profiles_manager
import web_search as web_search_mod

# ─── Layout constants ────────────────────────────────────────────
POLL_MS       = 30    # queue poll interval (ms)
MAX_CONTEXT   = 20    # last N messages sent to model
MAX_STORED    = 200   # messages stored per profile

# ─── Dark-theme colours ──────────────────────────────────────────
BG        = "#1e1e2e"
BG2       = "#181825"
BG3       = "#313244"
BG4       = "#45475a"
FG        = "#cdd6f4"
FG2       = "#a6adc8"
FG3       = "#6c7086"
USER_FG   = "#89b4fa"   # blue  — user messages
AI_FG     = "#cdd6f4"   # white — AI messages
THINK_FG  = "#6c7086"   # grey  — thinking indicator
ERR_FG    = "#f38ba8"   # red   — errors
ACCENT    = "#89b4fa"   # button highlight


def _btn(parent, text, cmd, bg=BG3, fg=FG, bold=False, **kw):
    font = ("Segoe UI", 9, "bold") if bold else ("Segoe UI", 9)
    return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                     activebackground=BG4, activeforeground=FG,
                     relief="flat", cursor="hand2", font=font, **kw)


# ─── Markdown rendering ──────────────────────────────────────────
_MD_INLINE = re.compile(
    r'\*\*\*(.+?)\*\*\*'
    r'|\*\*(.+?)\*\*'
    r'|\*(.+?)\*'
    r'|_(.+?)_'
    r'|`(.+?)`',
    re.DOTALL,
)


def _insert_inline(widget: tk.Text, text: str, base: str) -> None:
    """Insert text applying bold/italic/code inline markdown."""
    last = 0
    for m in _MD_INLINE.finditer(text):
        if m.start() > last:
            widget.insert(tk.END, text[last:m.start()], base)
        g1, g2, g3, g4, g5 = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        if g1:
            widget.insert(tk.END, g1, ("ai_bold", "italic"))
        elif g2:
            widget.insert(tk.END, g2, "ai_bold")
        elif g3 or g4:
            widget.insert(tk.END, (g3 or g4), "italic")
        elif g5:
            widget.insert(tk.END, g5, "code_inline")
        last = m.end()
    if last < len(text):
        widget.insert(tk.END, text[last:], base)


def _insert_markdown(widget: tk.Text, text: str) -> None:
    """Render a full markdown string into a Tkinter Text widget."""
    in_code = False
    code_buf: list = []
    for line in text.split('\n'):
        stripped = line.strip()
        # ── Code fence ──────────────────────────────────────────
        if stripped.startswith('```'):
            if not in_code:
                in_code = True
                code_buf = []
            else:
                in_code = False
                widget.insert(tk.END, '\n'.join(code_buf) + '\n', "code_block")
            continue
        if in_code:
            code_buf.append(line)
            continue
        # ── Headings ────────────────────────────────────────────
        if stripped.startswith('### '):
            _insert_inline(widget, stripped[4:], "h3")
            widget.insert(tk.END, '\n')
        elif stripped.startswith('## '):
            _insert_inline(widget, stripped[3:], "h2")
            widget.insert(tk.END, '\n')
        elif stripped.startswith('# '):
            _insert_inline(widget, stripped[2:], "h1")
            widget.insert(tk.END, '\n')
        # ── Horizontal rule ─────────────────────────────────────
        elif re.match(r'^[-*_]{3,}$', stripped):
            widget.insert(tk.END, '\u2500' * 56 + '\n', "rule")
        # ── Blockquote ──────────────────────────────────────────
        elif stripped.startswith('> '):
            _insert_inline(widget, stripped[2:], "quote")
            widget.insert(tk.END, '\n')
        elif stripped.startswith('>'):
            _insert_inline(widget, stripped[1:].lstrip(), "quote")
            widget.insert(tk.END, '\n')
        # ── Table separator row (skip) ───────────────────────────
        elif re.match(r'^\|[\s\-:|]+\|', stripped):
            pass
        # ── Table row ───────────────────────────────────────────
        elif stripped.startswith('|') and stripped.endswith('|'):
            widget.insert(tk.END, stripped + '\n', "table")
        # ── Bullet list ─────────────────────────────────────────
        elif re.match(r'^(\s*)[-*+]\s+', line):
            m = re.match(r'^(\s*)[-*+]\s+(.*)', line)
            depth = len(m.group(1)) // 2
            widget.insert(tk.END, '  ' * depth + '\u2022 ', "bullet")
            _insert_inline(widget, m.group(2), "ai_txt")
            widget.insert(tk.END, '\n')
        # ── Numbered list ───────────────────────────────────────
        elif re.match(r'^(\s*)\d+\.\s+', line):
            m = re.match(r'^(\s*)(\d+\.)\s+(.*)', line)
            depth = len(m.group(1)) // 2
            widget.insert(tk.END, '  ' * depth + m.group(2) + ' ', "bullet")
            _insert_inline(widget, m.group(3), "ai_txt")
            widget.insert(tk.END, '\n')
        # ── Blank line ──────────────────────────────────────────
        elif stripped == '':
            widget.insert(tk.END, '\n')
        # ── Normal paragraph ────────────────────────────────────
        else:
            _insert_inline(widget, line, "ai_txt")
            widget.insert(tk.END, '\n')



class App:
    # ══════════════════════════════════════════════════════════════
    # Init
    # ══════════════════════════════════════════════════════════════
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Llama Desktop")
        root.geometry("1100x720")
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # State
        self.profiles: Dict     = {}
        self.current_profile    = ""
        self.current_model      = ""
        self.history: List[Dict] = []

        self._sending           = False
        self._cancel_event      = threading.Event()
        self._q: queue.Queue    = queue.Queue()
        self._t_start           = 0.0
        self._token_count       = 0
        self._ai_buffer         = ""

        self._build_ui()
        self._load_data()
        self._poll_queue()

    # ══════════════════════════════════════════════════════════════
    # UI construction
    # ══════════════════════════════════════════════════════════════
    def _build_ui(self):
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        # ── Sidebar ───────────────────────────────────────────────
        sb = tk.Frame(self.root, bg=BG2, width=225)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)

        tk.Label(sb, text="⚙  Configuração", bg=BG2, fg=FG,
                 font=("Segoe UI", 11, "bold")).pack(pady=(14, 6), padx=14, anchor="w")

        # Model selector
        tk.Label(sb, text="Modelo", bg=BG2, fg=FG2,
                 font=("Segoe UI", 9)).pack(padx=14, anchor="w")
        self._model_var = tk.StringVar()
        self._model_cb  = ttk.Combobox(sb, textvariable=self._model_var,
                                       state="readonly", width=27)
        self._model_cb.pack(padx=14, pady=(2, 2), fill="x")
        self._model_cb.bind("<<ComboboxSelected>>", self._on_model_change)
        _btn(sb, "↻  Atualizar modelos", self._refresh_models).pack(
            padx=14, pady=(0, 12), fill="x")

        ttk.Separator(sb).pack(fill="x", padx=14, pady=4)

        # Profile selector
        tk.Label(sb, text="Perfil", bg=BG2, fg=FG2,
                 font=("Segoe UI", 9)).pack(padx=14, anchor="w")
        self._profile_var = tk.StringVar()
        self._profile_cb  = ttk.Combobox(sb, textvariable=self._profile_var,
                                         state="readonly", width=27)
        self._profile_cb.pack(padx=14, pady=(2, 6), fill="x")
        self._profile_cb.bind("<<ComboboxSelected>>", self._on_profile_change)

        pf = tk.Frame(sb, bg=BG2)
        pf.pack(padx=14, fill="x")
        for text, cmd in [("Novo", self._new_profile),
                          ("Editar", self._edit_profile),
                          ("Deletar", self._del_profile)]:
            _btn(pf, text, cmd).pack(side="left", fill="x", expand=True, padx=1)

        ttk.Separator(sb).pack(fill="x", padx=14, pady=10)

        # Actions
        tk.Label(sb, text="Ações", bg=BG2, fg=FG2,
                 font=("Segoe UI", 9)).pack(padx=14, anchor="w")
        for text, cmd in [("Limpar conversa", self._clear_history),
                          ("Exportar histórico", self._export_history)]:
            _btn(sb, text, cmd).pack(padx=14, pady=2, fill="x")

        # ── Main area ─────────────────────────────────────────────
        main = tk.Frame(self.root, bg=BG)
        main.grid(row=0, column=1, sticky="nsew")
        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)

        # Status bar
        self._status_var = tk.StringVar(value="Iniciando…")
        self._status_lbl = tk.Label(main, textvariable=self._status_var,
                                    bg=BG, fg=FG2, font=("Segoe UI", 9), anchor="w")
        self._status_lbl.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 0))

        # Chat area
        chat_frame = tk.Frame(main, bg=BG)
        chat_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        chat_frame.rowconfigure(0, weight=1)
        chat_frame.columnconfigure(0, weight=1)

        self._chat = tk.Text(
            chat_frame,
            wrap=tk.WORD, state="disabled",
            bg=BG, fg=FG,
            font=("Consolas", 10), padx=10, pady=8,
            borderwidth=0,
            highlightthickness=1, highlightbackground=BG3,
            selectbackground=BG4, selectforeground=FG,
            insertbackground=FG,
        )
        vscroll = ttk.Scrollbar(chat_frame, command=self._chat.yview)
        self._chat.configure(yscrollcommand=vscroll.set)
        self._chat.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")

        # Text tags — base
        self._chat.tag_configure("user_lbl",    foreground=USER_FG, font=("Consolas", 10, "bold"))
        self._chat.tag_configure("user_txt",    foreground=USER_FG)
        self._chat.tag_configure("ai_lbl",      foreground=ACCENT,  font=("Consolas", 10, "bold"))
        self._chat.tag_configure("ai_txt",      foreground=AI_FG)
        self._chat.tag_configure("ai_bold",     foreground=AI_FG,   font=("Consolas", 10, "bold"))
        self._chat.tag_configure("thinking",    foreground=THINK_FG, font=("Consolas", 9, "italic"))
        self._chat.tag_configure("error_txt",   foreground=ERR_FG)
        # Text tags — markdown
        self._chat.tag_configure("h1",          foreground=ACCENT,  font=("Consolas", 14, "bold"))
        self._chat.tag_configure("h2",          foreground=ACCENT,  font=("Consolas", 12, "bold"))
        self._chat.tag_configure("h3",          foreground=ACCENT,  font=("Consolas", 11, "bold"))
        self._chat.tag_configure("italic",      foreground=AI_FG,   font=("Consolas", 10, "italic"))
        self._chat.tag_configure("code_inline", foreground="#a6e3a1", background=BG3, font=("Consolas", 10))
        self._chat.tag_configure("code_block",  foreground="#a6e3a1", background=BG2, font=("Consolas", 10),
                                 lmargin1=20, lmargin2=20)
        self._chat.tag_configure("rule",        foreground=FG3)
        self._chat.tag_configure("quote",       foreground=FG2,    font=("Consolas", 10, "italic"),
                                 lmargin1=20, lmargin2=20)
        self._chat.tag_configure("bullet",      foreground=FG2,    lmargin1=10, lmargin2=24)
        self._chat.tag_configure("table",       foreground=AI_FG,  font=("Courier New", 9), background=BG2)

        # Input box
        self._input = tk.Text(
            main, height=3, wrap=tk.WORD,
            bg=BG3, fg=FG, insertbackground=FG,
            font=("Consolas", 10), padx=8, pady=6,
            borderwidth=0, highlightthickness=1, highlightbackground=BG4,
        )
        self._input.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 2))
        self._input.bind("<Return>", self._on_enter)

        # Button row
        br = tk.Frame(main, bg=BG)
        br.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 6))

        self._web_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            br, text="🌐 Busca Web", variable=self._web_var,
            bg=BG, fg=FG, selectcolor=BG3,
            activebackground=BG, activeforeground=FG, cursor="hand2",
        ).pack(side="left")

        self._stats_var = tk.StringVar(value="")
        tk.Label(br, textvariable=self._stats_var, bg=BG, fg=FG3,
                 font=("Segoe UI", 9)).pack(side="left", padx=10)

        self._stop_btn = _btn(br, "⏹  Parar", self._stop, state="disabled")
        self._stop_btn.pack(side="right", padx=(4, 0))

        self._send_btn = _btn(br, "Enviar  ▶", self._send,
                              bg=ACCENT, fg="#1e1e2e", bold=True)
        self._send_btn.pack(side="right")

    # ══════════════════════════════════════════════════════════════
    # Data loading
    # ══════════════════════════════════════════════════════════════
    def _load_data(self):
        self.profiles = profiles_manager.load_profiles()
        if self.profiles:
            self.current_profile = list(self.profiles.keys())[0]
            self.history = self.profiles[self.current_profile].setdefault("history", [])
        self._refresh_profile_combo()
        self._refresh_models()

    def _refresh_models(self):
        models = ollama_client.list_models()
        if models:
            self._model_cb["values"] = models
            if self.current_model and self.current_model in models:
                self._model_var.set(self.current_model)
            else:
                self.current_model = models[0]
                self._model_var.set(models[0])
            self._set_status(f"Pronto  |  {self.current_model}")
        else:
            self._model_cb["values"] = []
            self._model_var.set("")
            self._set_status(
                "⚠  Ollama offline.  Inicie com:  $env:OLLAMA_VULKAN=1; ollama serve",
                error=True,
            )

    def _on_model_change(self, _=None):
        self.current_model = self._model_var.get()
        self._set_status(f"Pronto  |  {self.current_model}")

    def _refresh_profile_combo(self):
        names = [v["name"] for v in self.profiles.values()]
        self._profile_cb["values"] = names
        if self.current_profile in self.profiles:
            self._profile_var.set(self.profiles[self.current_profile]["name"])
        elif self.profiles:
            self.current_profile = list(self.profiles.keys())[0]
            self._profile_var.set(self.profiles[self.current_profile]["name"])
        self._render_chat()

    def _on_profile_change(self, _=None):
        # Save current history before switching
        if self.current_profile:
            self.profiles[self.current_profile]["history"] = self.history
            profiles_manager.save_profiles(self.profiles)
        selected = self._profile_var.get()
        for k, v in self.profiles.items():
            if v["name"] == selected:
                self.current_profile = k
                self.history = v.setdefault("history", [])
                self._render_chat()
                return

    # ══════════════════════════════════════════════════════════════
    # Chat rendering
    # ══════════════════════════════════════════════════════════════
    def _render_chat(self):
        self._chat.config(state="normal")
        self._chat.delete("1.0", tk.END)
        for msg in self.history:
            role    = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                continue
            if role == "user":
                self._chat.insert(tk.END, "Você:  ", "user_lbl")
                self._chat.insert(tk.END, content + "\n\n", "user_txt")
            elif role == "assistant" and content:
                self._chat.insert(tk.END, "AI:  ", "ai_lbl")
                _insert_markdown(self._chat, content)
                self._chat.insert(tk.END, "\n")
        self._chat.config(state="disabled")
        self._chat.see(tk.END)

    # ══════════════════════════════════════════════════════════════
    # Queue polling — bridge between background thread and Tkinter
    # ══════════════════════════════════════════════════════════════
    def _poll_queue(self):
        try:
            for _ in range(100):          # drain up to 100 items per tick
                kind, data = self._q.get_nowait()
                if kind == "token":
                    self._ai_buffer += data
                    self._append_stream_token(data)
                elif kind == "thinking":
                    self._set_status(data)
                elif kind == "stats":
                    self._stats_var.set(data)
                elif kind == "done":
                    self._on_done()
                elif kind == "error":
                    self._on_error(data)
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll_queue)

    def _append_stream_token(self, token: str):
        """Fast path while streaming: append raw token to keep UI responsive."""
        if self.history and self.history[-1]["role"] == "assistant":
            self.history[-1]["content"] = self._ai_buffer
        self._chat.config(state="normal")
        self._chat.insert("end-1c", token, "ai_txt")
        self._chat.config(state="disabled")
        self._chat.see(tk.END)

    def _render_final_ai(self):
        """Final pass: replace streamed raw text with formatted markdown."""
        if self.history and self.history[-1]["role"] == "assistant":
            self.history[-1]["content"] = self._ai_buffer
        self._chat.config(state="normal")
        self._chat.delete("ai_msg_start", "end-1c")
        _insert_markdown(self._chat, self._ai_buffer)
        self._chat.config(state="disabled")
        self._chat.see(tk.END)

    def _on_done(self):
        self._sending = False
        self._send_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        self._input.config(state="normal")
        # Final clean render of AI response
        self._render_final_ai()
        self._chat.config(state="normal")
        if not self._ai_buffer.strip() and not self._cancel_event.is_set():
            self._chat.insert(tk.END, "[sem texto gerado]\n", "thinking")
        if self._cancel_event.is_set():
            self._chat.insert(tk.END, "[interrompido]\n", "thinking")
        self._chat.insert(tk.END, "\n")
        self._chat.config(state="disabled")
        self._chat.see(tk.END)
        # Trim + save
        if len(self.history) > MAX_STORED:
            self.history = self.history[-MAX_STORED:]
        self.profiles[self.current_profile]["history"] = self.history
        profiles_manager.save_profiles(self.profiles)
        elapsed = time.monotonic() - self._t_start
        tps = self._token_count / elapsed if elapsed > 0 else 0
        if self._cancel_event.is_set():
            self._stats_var.set(f"Interrompido  |  {self._token_count} tokens  |  {elapsed:.1f}s")
            self._set_status("Interrompido pelo usuário")
        else:
            self._stats_var.set(
                f"{self._token_count} tokens  |  {elapsed:.1f}s  |  {tps:.1f} tok/s"
            )
            profile_name = self.profiles.get(self.current_profile, {}).get("name", "")
            self._set_status(f"Pronto  |  {self.current_model}  |  {profile_name}")
            # Notificação sonora — só toca se a janela não estiver em foco
            try:
                winsound.MessageBeep(winsound.MB_OK)
            except Exception:
                pass

    def _on_error(self, msg: str):
        self._sending = False
        self._send_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        self._input.config(state="normal")
        # Remove empty assistant placeholder if no tokens arrived
        if (self.history
                and self.history[-1]["role"] == "assistant"
                and not self.history[-1]["content"]):
            self.history.pop()
        self._render_chat()
        self._set_status(f"Erro: {msg}", error=True)

    # ══════════════════════════════════════════════════════════════
    # Sending
    # ══════════════════════════════════════════════════════════════
    def _on_enter(self, event):
        if event.state & 0x1:   # Shift held → insert newline normally
            return
        self._send()
        return "break"

    def _send(self):
        if self._sending:
            return
        text = self._input.get("1.0", tk.END).strip()
        if not text:
            return
        model = self._model_var.get()
        if not model:
            self._set_status("Selecione um modelo", error=True)
            return
        if not self.current_profile:
            self._set_status("Selecione um perfil", error=True)
            return

        self._input.delete("1.0", tk.END)
        self._sending = True
        self._cancel_event.clear()
        self._token_count = 0
        self._t_start = time.monotonic()
        self._send_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._input.config(state="disabled")
        self._stats_var.set("")
        self._ai_buffer = ""

        # Append to history and chat display
        self.history.append({"role": "user",      "content": text})
        self.history.append({"role": "assistant", "content": ""})
        self._chat.config(state="normal")
        self._chat.insert(tk.END, "Você:  ", "user_lbl")
        self._chat.insert(tk.END, text + "\n\n", "user_txt")
        self._chat.insert(tk.END, "AI:  ", "ai_lbl")
        # Mark where AI content starts — used by _rerender_ai to re-render from here
        self._chat.mark_set("ai_msg_start", "end-1c")
        self._chat.mark_gravity("ai_msg_start", "left")
        self._chat.config(state="disabled")
        self._chat.see(tk.END)

        # Build API messages: system prompt + last MAX_CONTEXT history items
        # (self.history[:-1] excludes the empty assistant placeholder)
        profile = self.profiles.get(self.current_profile, {})
        sys_prompt = profile.get("system_prompt", "")
        messages = []
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        for msg in self.history[:-1][-MAX_CONTEXT:]:
            if msg["role"] != "system":
                messages.append(msg)

        self._set_status(f"Gerando…  [{model}]")
        threading.Thread(
            target=self._worker,
            args=(model, messages, text),
            daemon=True,
        ).start()

    def _worker(self, model: str, messages: list, user_text: str):
        try:
            # Web search: inject results as context
            if self._web_var.get():
                self._q.put(("stats", "Buscando na web…"))
                ctx = web_search_mod.search(user_text)
                if ctx:
                    messages = list(messages)
                    for i in range(len(messages) - 1, -1, -1):
                        if messages[i]["role"] == "user":
                            messages[i] = {
                                "role": "user",
                                "content": (
                                    f"Resultados da busca web:\n{ctx}\n\n"
                                    f"Pergunta: {messages[i]['content']}"
                                ),
                            }
                            break

            thinking_chars = [0]
            last_think_ts  = [time.monotonic()]

            def on_thinking(t: str):
                thinking_chars[0] += len(t)
                now = time.monotonic()
                if now - last_think_ts[0] >= 1.0:
                    elapsed = now - self._t_start
                    self._q.put((
                        "thinking",
                        f"Pensando…  [{model}]  {thinking_chars[0]:,} chars  |  {elapsed:.0f}s",
                    ))
                    last_think_ts[0] = now

            def on_token(token: str):
                self._token_count += 1
                self._q.put(("token", token))
                if self._token_count == 1:
                    elapsed = time.monotonic() - self._t_start
                    self._q.put((
                        "thinking",
                        f"Gerando…  [{model}]  primeiro token em {elapsed:.1f}s",
                    ))
                elif self._token_count % 25 == 0:
                    elapsed = time.monotonic() - self._t_start
                    self._q.put(("stats", f"{self._token_count} tokens  |  {elapsed:.1f}s"))

            ollama_client.stream_chat(
                model=model,
                messages=messages,
                on_token=on_token,
                on_thinking=on_thinking,
                cancel_event=self._cancel_event,
            )

        except Exception as exc:
            self._q.put(("error", str(exc)))
            return

        self._q.put(("done", None))

    def _stop(self):
        if not self._sending:
            return
        self._cancel_event.set()
        self._set_status("Cancelando…")

    # ══════════════════════════════════════════════════════════════
    # Profile management
    # ══════════════════════════════════════════════════════════════
    def _profile_dialog(self, title: str,
                        name: str = "", prompt: str = "") -> tuple:
        result = [None, None]

        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.geometry("540x440")
        dlg.configure(bg=BG)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="Nome:", bg=BG, fg=FG).pack(padx=14, pady=(14, 2), anchor="w")
        name_var = tk.StringVar(value=name)
        tk.Entry(dlg, textvariable=name_var, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat",
                 font=("Consolas", 10)).pack(padx=14, pady=(0, 8), fill="x")

        tk.Label(dlg, text="System Prompt:", bg=BG, fg=FG).pack(padx=14, anchor="w")
        prompt_box = tk.Text(dlg, wrap=tk.WORD, bg=BG3, fg=FG,
                             insertbackground=FG, relief="flat",
                             font=("Consolas", 10), padx=8, pady=6)
        prompt_box.insert("1.0", prompt)
        prompt_box.pack(padx=14, pady=(2, 8), fill="both", expand=True)

        def save():
            n = name_var.get().strip()
            p = prompt_box.get("1.0", tk.END).strip()
            if not n or not p:
                messagebox.showerror("Erro", "Nome e prompt são obrigatórios.", parent=dlg)
                return
            result[0], result[1] = n, p
            dlg.destroy()

        bf = tk.Frame(dlg, bg=BG)
        bf.pack(fill="x", padx=14, pady=8)
        _btn(bf, "Cancelar", dlg.destroy).pack(side="right", padx=4)
        _btn(bf, "Salvar", save, bg=ACCENT, fg="#1e1e2e", bold=True).pack(side="right")

        self.root.wait_window(dlg)
        return tuple(result)

    def _new_profile(self):
        name, prompt = self._profile_dialog("Novo Perfil")
        if not name:
            return
        key = name.lower().replace(" ", "_").replace("/", "_")
        base, i = key, 1
        while key in self.profiles:
            key = f"{base}_{i}"; i += 1
        self.profiles[key] = {"name": name, "system_prompt": prompt, "history": []}
        profiles_manager.save_profiles(self.profiles)
        self.current_profile = key
        self.history = self.profiles[key]["history"]
        self._refresh_profile_combo()

    def _edit_profile(self):
        if not self.current_profile:
            return
        p = self.profiles[self.current_profile]
        name, prompt = self._profile_dialog(
            "Editar Perfil", p["name"], p.get("system_prompt", ""))
        if not name:
            return
        self.profiles[self.current_profile]["name"]          = name
        self.profiles[self.current_profile]["system_prompt"] = prompt
        profiles_manager.save_profiles(self.profiles)
        self._refresh_profile_combo()

    def _del_profile(self):
        if not self.current_profile or len(self.profiles) <= 1:
            messagebox.showwarning("Aviso", "Não é possível deletar o único perfil.",
                                   parent=self.root)
            return
        name = self.profiles[self.current_profile]["name"]
        if not messagebox.askyesno("Confirmar", f"Deletar perfil '{name}'?",
                                   parent=self.root):
            return
        del self.profiles[self.current_profile]
        profiles_manager.save_profiles(self.profiles)
        self.current_profile = list(self.profiles.keys())[0]
        self.history = self.profiles[self.current_profile].setdefault("history", [])
        self._refresh_profile_combo()

    def _clear_history(self):
        if not messagebox.askyesno("Confirmar", "Limpar histórico desta conversa?",
                                   parent=self.root):
            return
        self.history.clear()
        if self.current_profile:
            self.profiles[self.current_profile]["history"] = []
            profiles_manager.save_profiles(self.profiles)
        self._render_chat()

    def _export_history(self):
        path = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension=".txt",
            filetypes=[("Arquivo de texto", "*.txt"), ("Todos os arquivos", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                for msg in self.history:
                    if msg["role"] == "system":
                        continue
                    label = "Você" if msg["role"] == "user" else "AI"
                    f.write(f"{label}:\n{msg['content']}\n\n{'─' * 60}\n\n")
            self._set_status(f"Exportado: {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Erro ao exportar", str(exc), parent=self.root)

    # ══════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════
    def _set_status(self, text: str, error: bool = False):
        self._status_var.set(text)
        self._status_lbl.config(fg=ERR_FG if error else FG2)

    def _on_close(self):
        if self.current_profile and self.profiles:
            self.profiles[self.current_profile]["history"] = self.history
            profiles_manager.save_profiles(self.profiles)
        self.root.destroy()


# ─── Entry point ─────────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
