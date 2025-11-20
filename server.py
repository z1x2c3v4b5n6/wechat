import json
import socket
import threading
from datetime import datetime
from typing import Dict, Optional

from database import Database, bootstrap_default_admin

HOST = "0.0.0.0"
PORT = 9009


def format_message(action: str, status: str = "ok", **data):
    payload = {"action": action, "status": status}
    payload.update(data)
    return json.dumps(payload) + "\n"


class ChatServer:
    def __init__(self, host: str = HOST, port: int = PORT) -> None:
        self.host = host
        self.port = port
        self.db = Database()
        bootstrap_default_admin(self.db)
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.online: Dict[int, socket.socket] = {}
        self.online_lock = threading.Lock()

    def start(self) -> None:
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(10)
        print(f"Server listening on {self.host}:{self.port}")
        while True:
            conn, addr = self.server_socket.accept()
            handler = ClientHandler(conn, addr, self)
            handler.daemon = True
            handler.start()

    def broadcast(self, content: str, sender_id: Optional[int] = None) -> None:
        with self.online_lock:
            for uid, conn in list(self.online.items()):
                if sender_id is not None and uid == sender_id:
                    continue
                try:
                    conn.sendall(format_message("announcement", message=content).encode())
                except OSError:
                    pass

    def send_to_user(self, user_id: int, payload: str) -> bool:
        with self.online_lock:
            conn = self.online.get(user_id)
        if not conn:
            return False
        try:
            conn.sendall(payload.encode())
            return True
        except OSError:
            return False

    def set_online(self, user_id: int, conn: socket.socket) -> None:
        with self.online_lock:
            self.online[user_id] = conn

    def set_offline(self, user_id: Optional[int]) -> None:
        if user_id is None:
            return
        with self.online_lock:
            self.online.pop(user_id, None)


class ClientHandler(threading.Thread):
    def __init__(self, conn: socket.socket, addr, server: ChatServer) -> None:
        super().__init__()
        self.conn = conn
        self.addr = addr
        self.server = server
        self.user: Optional[Dict] = None
        self.alive = True

    def run(self) -> None:
        print(f"New connection from {self.addr}")
        buf = ""
        try:
            while self.alive:
                data = self.conn.recv(4096)
                if not data:
                    break
                buf += data.decode()
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if line:
                        self.handle_request(line)
        except ConnectionResetError:
            pass
        finally:
            self.cleanup()

    def handle_request(self, raw: str) -> None:
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            self.conn.sendall(format_message("error", "error", message="格式错误").encode())
            return

        action = req.get("action")
        if action == "register":
            self.handle_register(req)
        elif action == "login":
            self.handle_login(req)
        elif action == "logout":
            self.alive = False
        elif not self.user:
            self.conn.sendall(format_message("auth", "error", message="请先登录").encode())
        else:
            handler = getattr(self, f"handle_{action}", None)
            if handler:
                handler(req)
            else:
                self.conn.sendall(format_message("error", "error", message="未知指令").encode())

    def handle_register(self, req: Dict) -> None:
        ok, msg = self.server.db.register_user(
            req.get("username", ""), req.get("password", ""), req.get("nickname", "") or req.get("username", "")
        )
        self.conn.sendall(format_message("register", "ok" if ok else "error", message=msg).encode())

    def handle_login(self, req: Dict) -> None:
        user = self.server.db.authenticate(req.get("username", ""), req.get("password", ""))
        if not user:
            self.conn.sendall(format_message("login", "error", message="账号或密码错误").encode())
            return
        self.user = user
        self.server.set_online(user["id"], self.conn)
        self.server.db.log_login(user["id"], "login")
        offline = self.server.db.fetch_offline_messages(user["id"])
        for msg in offline:
            self.server.db.mark_message_delivered(msg["id"])
        friends = self.server.db.list_friends(user["id"])
        groups = self.server.db.list_groups(user["id"])
        payload = format_message(
            "login",
            user=user,
            friends=friends,
            groups=groups,
            offline_messages=offline,
            message="登录成功",
        )
        self.conn.sendall(payload.encode())
        self.notify_status_change(True)

    def handle_add_friend(self, req: Dict) -> None:
        ok, msg = self.server.db.add_friend(self.user["id"], req.get("friend_username", ""))
        self.conn.sendall(format_message("add_friend", "ok" if ok else "error", message=msg).encode())

    def handle_remove_friend(self, req: Dict) -> None:
        self.server.db.remove_friend(self.user["id"], int(req.get("friend_id", 0)))
        self.conn.sendall(format_message("remove_friend", message="已删除").encode())

    def handle_list_friends(self, _: Dict) -> None:
        friends = self.server.db.list_friends(self.user["id"])
        self.conn.sendall(format_message("list_friends", friends=friends).encode())

    def handle_update_profile(self, req: Dict) -> None:
        self.server.db.update_profile(self.user["id"], **req.get("profile", {}))
        self.conn.sendall(format_message("update_profile", message="资料已更新").encode())

    def handle_create_group(self, req: Dict) -> None:
        gid = self.server.db.create_group(self.user["id"], req.get("name", "新建群聊"))
        self.conn.sendall(format_message("create_group", group_id=gid).encode())

    def handle_join_group(self, req: Dict) -> None:
        gid = int(req.get("group_id", 0))
        ok = self.server.db.join_group(self.user["id"], gid)
        self.conn.sendall(format_message("join_group", status="ok" if ok else "error").encode())

    def handle_leave_group(self, req: Dict) -> None:
        gid = int(req.get("group_id", 0))
        self.server.db.leave_group(self.user["id"], gid)
        self.conn.sendall(format_message("leave_group", message="已退出群聊").encode())

    def handle_send_message(self, req: Dict) -> None:
        recipient_type = req.get("recipient_type", "user")
        recipient_id = int(req.get("recipient_id", 0))
        content_type = req.get("content_type", "text")
        content = req.get("content", "")
        created_at = datetime.utcnow().isoformat()
        delivered = 0

        message_ids = []

        if recipient_type == "user":
            sent = self.server.send_to_user(
                recipient_id,
                format_message(
                    "new_message",
                    message_id=0,
                    data={
                        "sender": self.user,
                        "recipient_type": recipient_type,
                        "recipient_id": recipient_id,
                        "content_type": content_type,
                        "content": content,
                        "created_at": created_at,
                    },
                ),
            )
            delivered = 1 if sent else 0
            message_ids.append(
                self.server.db.save_message(
                    self.user["id"], recipient_type, recipient_id, content_type, content, delivered
                )
            )
        elif recipient_type == "group":
            member_ids = self.server.db.get_group_members(recipient_id)
            for uid in member_ids:
                sent = False
                if uid != self.user["id"]:
                    payload = format_message(
                        "new_message",
                        data={
                            "sender": self.user,
                            "recipient_type": recipient_type,
                            "recipient_id": recipient_id,
                            "content_type": content_type,
                            "content": content,
                            "created_at": created_at,
                        },
                    )
                    sent = self.server.send_to_user(uid, payload)
                message_ids.append(
                    self.server.db.save_message(
                        self.user["id"], recipient_type, recipient_id, content_type, content, 1 if sent or uid == self.user["id"] else 0
                    )
                )

        ack_id = message_ids[-1] if message_ids else 0
        self.conn.sendall(format_message("send_message", message_id=ack_id).encode())

    def handle_broadcast(self, req: Dict) -> None:
        if self.user.get("role") != "admin":
            self.conn.sendall(format_message("broadcast", "error", message="无权限").encode())
            return
        self.server.broadcast(req.get("content", ""), sender_id=self.user["id"])
        self.conn.sendall(format_message("broadcast", message="公告已发送").encode())

    def handle_list_groups(self, _: Dict) -> None:
        groups = self.server.db.list_groups(self.user["id"])
        self.conn.sendall(format_message("list_groups", groups=groups).encode())

    def cleanup(self) -> None:
        if self.user:
            self.server.db.log_login(self.user["id"], "logout")
            self.notify_status_change(False)
        self.server.set_offline(self.user["id"] if self.user else None)
        try:
            self.conn.close()
        except OSError:
            pass
        print(f"Connection closed {self.addr}")

    def notify_status_change(self, online: bool) -> None:
        if not self.user:
            return
        status_msg = format_message(
            "status",
            data={"user_id": self.user["id"], "online": online},
        )
        with self.server.online_lock:
            for uid, conn in self.server.online.items():
                if uid == self.user["id"]:
                    continue
                try:
                    conn.sendall(status_msg.encode())
                except OSError:
                    pass


if __name__ == "__main__":
    ChatServer().start()
