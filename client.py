import base64
import json
import os
import queue
import socket
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext
from typing import Dict, List, Optional

SERVER_HOST = os.environ.get("CHAT_HOST", "127.0.0.1")
SERVER_PORT = int(os.environ.get("CHAT_PORT", 9009))


class NetworkClient(threading.Thread):
    def __init__(self, host: str, port: int, inbox: "queue.Queue[Dict]") -> None:
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.inbox = inbox
        self.conn: Optional[socket.socket] = None
        self.buffer = ""
        self.running = True

    def connect(self) -> None:
        self.conn = socket.create_connection((self.host, self.port))
        self.start()

    def run(self) -> None:
        if not self.conn:
            return
        try:
            while self.running:
                data = self.conn.recv(4096)
                if not data:
                    break
                self.buffer += data.decode()
                while "\n" in self.buffer:
                    line, self.buffer = self.buffer.split("\n", 1)
                    if line:
                        self.inbox.put(json.loads(line))
        except OSError:
            pass
        finally:
            self.inbox.put({"action": "disconnect"})

    def send(self, payload: Dict) -> None:
        if not self.conn:
            return
        try:
            self.conn.sendall((json.dumps(payload) + "\n").encode())
        except OSError:
            messagebox.showerror("网络异常", "与服务器的连接已断开")

    def close(self) -> None:
        self.running = False
        if self.conn:
            try:
                self.conn.close()
            except OSError:
                pass


class ChatGUI:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("校园即时通信 - 客户端")
        self.inbox: "queue.Queue[Dict]" = queue.Queue()
        self.network = NetworkClient(SERVER_HOST, SERVER_PORT, self.inbox)
        self.user: Optional[Dict] = None
        self.friends: List[Dict] = []
        self.groups: List[Dict] = []
        self.current_target: Optional[Dict] = None  # {type: user/group, id, name}
        self.chat_logs: Dict[str, List[str]] = {}
        self._build_login()
        self.poll_inbox()

    def _build_login(self) -> None:
        frame = tk.Frame(self.root)
        frame.pack(padx=10, pady=10)
        tk.Label(frame, text="用户名").grid(row=0, column=0, sticky="e")
        tk.Label(frame, text="密码").grid(row=1, column=0, sticky="e")
        self.entry_user = tk.Entry(frame)
        self.entry_pass = tk.Entry(frame, show="*")
        self.entry_user.grid(row=0, column=1)
        self.entry_pass.grid(row=1, column=1)
        tk.Button(frame, text="登录", command=self.login).grid(row=2, column=0, pady=6)
        tk.Button(frame, text="注册", command=self.register).grid(row=2, column=1, pady=6)

    def _build_main(self) -> None:
        self.root.title(f"校园即时通信 - {self.user['nickname']}")
        for child in list(self.root.children.values()):
            child.destroy()
        container = tk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(container)
        left.pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(left, text="联系人/群聊").pack()
        self.list_contacts = tk.Listbox(left, width=28)
        self.list_contacts.pack(fill=tk.BOTH, expand=True)
        self.list_contacts.bind("<<ListboxSelect>>", self.switch_chat)
        tk.Button(left, text="刷新", command=self.refresh_contacts).pack(fill=tk.X)
        tk.Button(left, text="新建群聊", command=self.create_group).pack(fill=tk.X)
        tk.Button(left, text="加入群聊", command=self.join_group).pack(fill=tk.X)
        tk.Button(left, text="退出群聊", command=self.leave_group).pack(fill=tk.X)
        tk.Button(left, text="添加好友", command=self.prompt_add_friend).pack(fill=tk.X)

        right = tk.Frame(container)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.chat_title = tk.Label(right, text="未选择会话", font=("Arial", 12, "bold"))
        self.chat_title.pack()
        self.text_area = scrolledtext.ScrolledText(right, state=tk.DISABLED, width=60, height=25)
        self.text_area.pack(fill=tk.BOTH, expand=True)

        entry_frame = tk.Frame(right)
        entry_frame.pack(fill=tk.X)
        self.entry_message = tk.Entry(entry_frame)
        self.entry_message.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(entry_frame, text="发送", command=self.send_text).pack(side=tk.RIGHT)
        tk.Button(entry_frame, text="发送文件", command=self.send_file).pack(side=tk.RIGHT)

        self.status_label = tk.Label(self.root, text="连接中...", anchor="w")
        self.status_label.pack(fill=tk.X)
        self.render_contacts()

    def poll_inbox(self) -> None:
        while not self.inbox.empty():
            msg = self.inbox.get()
            self.handle_message(msg)
        self.root.after(100, self.poll_inbox)

    def login(self) -> None:
        try:
            self.network.connect()
        except OSError as exc:
            messagebox.showerror("连接失败", str(exc))
            return
        payload = {
            "action": "login",
            "username": self.entry_user.get().strip(),
            "password": self.entry_pass.get().strip(),
        }
        self.network.send(payload)

    def register(self) -> None:
        try:
            self.network.connect()
        except OSError as exc:
            messagebox.showerror("连接失败", str(exc))
            return
        payload = {
            "action": "register",
            "username": self.entry_user.get().strip(),
            "password": self.entry_pass.get().strip(),
            "nickname": self.entry_user.get().strip(),
        }
        self.network.send(payload)

    def refresh_contacts(self) -> None:
        if not self.user:
            return
        self.network.send({"action": "list_friends"})
        self.network.send({"action": "list_groups"})

    def create_group(self) -> None:
        name = tk.simpledialog.askstring("新建群聊", "群名称")
        if name:
            self.network.send({"action": "create_group", "name": name})

    def join_group(self) -> None:
        gid = tk.simpledialog.askinteger("加入群聊", "输入群ID")
        if gid:
            self.network.send({"action": "join_group", "group_id": gid})

    def leave_group(self) -> None:
        if self.current_target and self.current_target.get("type") == "group":
            self.network.send({"action": "leave_group", "group_id": self.current_target["id"]})
        else:
            messagebox.showinfo("提示", "请先在列表选择要退出的群聊")

    def prompt_add_friend(self) -> None:
        username = tk.simpledialog.askstring("添加好友", "好友用户名")
        if username:
            self.network.send({"action": "add_friend", "friend_username": username})

    def switch_chat(self, _: object) -> None:
        sel = self.list_contacts.curselection()
        if not sel:
            return
        item = self.list_contacts.get(sel[0])
        contact_type, contact_id, name = item.split("|", 2)
        self.current_target = {"type": contact_type, "id": int(contact_id), "name": name}
        self.chat_title.config(text=f"与 {name} 的对话" if contact_type == "user" else f"群聊: {name}")
        key = f"{contact_type}:{contact_id}"
        self.text_area.configure(state=tk.NORMAL)
        self.text_area.delete("1.0", tk.END)
        for line in self.chat_logs.get(key, []):
            self.text_area.insert(tk.END, line + "\n")
        self.text_area.configure(state=tk.DISABLED)

    def append_chat(self, contact_type: str, contact_id: int, line: str) -> None:
        key = f"{contact_type}:{contact_id}"
        self.chat_logs.setdefault(key, []).append(line)
        if self.current_target and self.current_target["type"] == contact_type and self.current_target["id"] == contact_id:
            self.text_area.configure(state=tk.NORMAL)
            self.text_area.insert(tk.END, line + "\n")
            self.text_area.configure(state=tk.DISABLED)

    def send_text(self) -> None:
        if not self.current_target:
            messagebox.showinfo("提示", "请先选择会话")
            return
        content = self.entry_message.get().strip()
        if not content:
            return
        self.entry_message.delete(0, tk.END)
        payload = {
            "action": "send_message",
            "recipient_type": self.current_target["type"],
            "recipient_id": self.current_target["id"],
            "content_type": "text",
            "content": content,
        }
        self.network.send(payload)
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.append_chat(self.current_target["type"], self.current_target["id"], f"我 [{timestamp}]: {content}")

    def send_file(self) -> None:
        if not self.current_target:
            messagebox.showinfo("提示", "请先选择会话")
            return
        file_path = filedialog.askopenfilename()
        if not file_path:
            return
        with open(file_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        filename = os.path.basename(file_path)
        payload = {
            "action": "send_message",
            "recipient_type": self.current_target["type"],
            "recipient_id": self.current_target["id"],
            "content_type": "file",
            "content": json.dumps({"name": filename, "data": data}),
        }
        self.network.send(payload)
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.append_chat(
            self.current_target["type"],
            self.current_target["id"],
            f"我 [{timestamp}]: 发送文件 {filename} (已上传)",
        )

    def handle_message(self, msg: Dict) -> None:
        action = msg.get("action")
        status = msg.get("status", "ok")
        if action == "login" and status == "ok":
            self.user = msg.get("user")
            self.friends = msg.get("friends", [])
            self.groups = msg.get("groups", [])
            self._build_main()
            self.status_label.config(text="登录成功")
            for offline in msg.get("offline_messages", []):
                self.render_incoming(offline, offline=True)
        elif action == "register":
            messagebox.showinfo("注册", msg.get("message"))
        elif action == "auth" and status == "error":
            messagebox.showerror("认证失败", msg.get("message"))
        elif action == "list_friends":
            self.friends = msg.get("friends", [])
            self.render_contacts()
        elif action == "list_groups":
            self.groups = msg.get("groups", [])
            self.render_contacts()
        elif action == "add_friend":
            messagebox.showinfo("添加好友", msg.get("message"))
            self.refresh_contacts()
        elif action == "create_group":
            messagebox.showinfo("群聊", f"已创建群聊 ID: {msg.get('group_id')}")
            self.refresh_contacts()
        elif action == "join_group":
            messagebox.showinfo("群聊", "加入群聊成功" if status == "ok" else "加入失败")
            self.refresh_contacts()
        elif action == "leave_group":
            messagebox.showinfo("群聊", msg.get("message"))
            self.refresh_contacts()
        elif action == "new_message":
            self.render_incoming(msg.get("data", {}))
        elif action == "announcement":
            messagebox.showinfo("系统公告", msg.get("message"))
        elif action == "status":
            state = "上线" if msg.get("data", {}).get("online") else "离线"
            self.status_label.config(text=f"用户 {msg.get('data', {}).get('user_id')} {state}")
        elif action == "disconnect":
            self.status_label.config(text="连接已断开")
        elif status == "error":
            messagebox.showerror("提示", msg.get("message", "出错了"))

    def render_contacts(self) -> None:
        if not hasattr(self, "list_contacts"):
            return
        self.list_contacts.delete(0, tk.END)
        for f in self.friends:
            label = f"{f['nickname']} ({'在线' if f.get('online') else '离线'})"
            self.list_contacts.insert(tk.END, f"user|{f['id']}|{label}")
        for g in self.groups:
            self.list_contacts.insert(tk.END, f"group|{g['id']}|群:{g['name']}")

    def render_incoming(self, payload: Dict, offline: bool = False) -> None:
        sender = payload.get("sender", {})
        content_type = payload.get("content_type", "text")
        content = payload.get("content", "")
        target_type = payload.get("recipient_type", "user")
        target_id = payload.get("recipient_id", 0)
        name = sender.get("nickname", sender.get("username", ""))
        timestamp = payload.get("created_at") or datetime.now().strftime("%H:%M:%S")
        prefix = "[离线] " if offline else ""
        if content_type == "file":
            info = json.loads(content)
            line = f"{prefix}{name} [{timestamp}]: 发送文件 {info['name']} (保存到临时目录)"
            self.save_file(info)
        else:
            line = f"{prefix}{name} [{timestamp}]: {content}"
        self.append_chat(target_type, target_id, line)

    def save_file(self, info: Dict[str, str]) -> None:
        data = base64.b64decode(info["data"])
        tmp_dir = os.path.join(os.path.dirname(__file__), "downloads")
        os.makedirs(tmp_dir, exist_ok=True)
        path = os.path.join(tmp_dir, info["name"])
        with open(path, "wb") as f:
            f.write(data)
        self.status_label.config(text=f"文件已保存: {path}")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    ChatGUI().run()
