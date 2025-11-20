import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

DB_PATH = os.path.join(os.path.dirname(__file__), "chat.db")


class Database:
    def __init__(self, path: str = DB_PATH) -> None:
        self.path = path
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                nickname TEXT NOT NULL,
                avatar TEXT,
                signature TEXT,
                role TEXT DEFAULT 'user'
            );

            CREATE TABLE IF NOT EXISTS friends (
                user_id INTEGER NOT NULL,
                friend_id INTEGER NOT NULL,
                UNIQUE(user_id, friend_id)
            );

            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                owner_id INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS group_members (
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                UNIQUE(group_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                recipient_type TEXT NOT NULL,
                recipient_id INTEGER NOT NULL,
                content_type TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                delivered INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS login_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def register_user(self, username: str, password: str, nickname: str) -> Tuple[bool, str]:
        try:
            self.conn.execute(
                "INSERT INTO users(username, password, nickname) VALUES (?, ?, ?)",
                (username, password, nickname),
            )
            self.conn.commit()
            return True, "注册成功"
        except sqlite3.IntegrityError:
            return False, "用户名已存在"

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM users WHERE username=? AND password=?", (username, password)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def update_profile(self, user_id: int, **kwargs: Any) -> None:
        allowed = {k: v for k, v in kwargs.items() if k in {"nickname", "avatar", "signature"}}
        if not allowed:
            return
        sets = ",".join(f"{k}=?" for k in allowed)
        values = list(allowed.values())
        values.append(user_id)
        self.conn.execute(f"UPDATE users SET {sets} WHERE id=?", values)
        self.conn.commit()

    def add_friend(self, user_id: int, friend_username: str) -> Tuple[bool, str]:
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM users WHERE username=?", (friend_username,))
        row = cur.fetchone()
        if not row:
            return False, "好友不存在"
        fid = row[0]
        if fid == user_id:
            return False, "不能添加自己"
        try:
            cur.execute("INSERT INTO friends(user_id, friend_id) VALUES (?, ?)", (user_id, fid))
            cur.execute("INSERT INTO friends(user_id, friend_id) VALUES (?, ?)", (fid, user_id))
            self.conn.commit()
            return True, "已添加"
        except sqlite3.IntegrityError:
            return False, "已在好友列表"

    def remove_friend(self, user_id: int, friend_id: int) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM friends WHERE user_id=? AND friend_id=?", (user_id, friend_id))
        cur.execute("DELETE FROM friends WHERE user_id=? AND friend_id=?", (friend_id, user_id))
        self.conn.commit()

    def list_friends(self, user_id: int) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT u.* FROM users u
            JOIN friends f ON f.friend_id = u.id
            WHERE f.user_id=?
            ORDER BY u.nickname
            """,
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    def create_group(self, owner_id: int, name: str) -> int:
        cur = self.conn.cursor()
        cur.execute("INSERT INTO groups(name, owner_id) VALUES (?, ?)", (name, owner_id))
        gid = cur.lastrowid
        cur.execute(
            "INSERT OR IGNORE INTO group_members(group_id, user_id) VALUES (?, ?)",
            (gid, owner_id),
        )
        self.conn.commit()
        return gid

    def join_group(self, user_id: int, group_id: int) -> bool:
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO group_members(group_id, user_id) VALUES (?, ?)",
                (group_id, user_id),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def leave_group(self, user_id: int, group_id: int) -> None:
        self.conn.execute(
            "DELETE FROM group_members WHERE group_id=? AND user_id=?", (group_id, user_id)
        )
        self.conn.commit()

    def list_groups(self, user_id: int) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT g.* FROM groups g
            JOIN group_members m ON m.group_id = g.id
            WHERE m.user_id=?
            ORDER BY g.name
            """,
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_group_members(self, group_id: int) -> List[int]:
        cur = self.conn.cursor()
        cur.execute("SELECT user_id FROM group_members WHERE group_id=?", (group_id,))
        return [r[0] for r in cur.fetchall()]

    def save_message(
        self,
        sender_id: int,
        recipient_type: str,
        recipient_id: int,
        content_type: str,
        content: str,
        delivered: int = 0,
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO messages(sender_id, recipient_type, recipient_id, content_type, content, created_at, delivered)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sender_id,
                recipient_type,
                recipient_id,
                content_type,
                content,
                datetime.utcnow().isoformat(),
                delivered,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def fetch_offline_messages(self, user_id: int) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT * FROM messages
            WHERE delivered=0 AND (
                (recipient_type='user' AND recipient_id=?) OR
                (recipient_type='group' AND recipient_id IN (
                    SELECT group_id FROM group_members WHERE user_id=?
                ))
            )
            ORDER BY created_at
            """,
            (user_id, user_id),
        )
        return [dict(r) for r in cur.fetchall()]

    def mark_message_delivered(self, message_id: int) -> None:
        self.conn.execute("UPDATE messages SET delivered=1 WHERE id=?", (message_id,))
        self.conn.commit()

    def log_login(self, user_id: int, action: str) -> None:
        self.conn.execute(
            "INSERT INTO login_logs(user_id, action, timestamp) VALUES (?, ?, ?)",
            (user_id, action, datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


def bootstrap_default_admin(db: Database) -> None:
    cur = db.conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        db.register_user("admin", "admin", "校园管理员")
        db.conn.execute("UPDATE users SET role='admin' WHERE username='admin'")
        db.conn.commit()


__all__ = ["Database", "bootstrap_default_admin"]
