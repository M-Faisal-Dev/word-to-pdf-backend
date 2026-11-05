"""Passwords are received on stdin, never command-line arguments or log output."""
import json
import sys

import pymupdf


class PasswordError(Exception):
    pass


class AlreadyProtected(Exception):
    pass


class NotProtected(Exception):
    pass


def secure(source: str, destination: str, max_pages: int, action: str, password: str):
    if action not in {"lock", "unlock"}:
        raise ValueError("Invalid action")
    length = len(password.encode("utf-8"))
    if "\0" in password or (not 8 <= length <= 40 if action == "lock" else not 1 <= length <= 127):
        raise ValueError("Invalid password")
    with pymupdf.open(source) as document:
        encrypted = document.needs_pass or bool((document.metadata or {}).get("encryption"))
        if action == "lock" and encrypted:
            raise AlreadyProtected()
        if action == "unlock":
            if not encrypted:
                raise NotProtected()
            if not document.authenticate(password):
                raise PasswordError()
        if not 0 < document.page_count <= max_pages:
            raise ValueError("Page limit exceeded")
        count = document.page_count
        options = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": password, "owner_pw": password, "permissions": -1} if action == "lock" else {"encryption": pymupdf.PDF_ENCRYPT_NONE}
        document.save(destination, garbage=4, deflate=True, **options)
    with pymupdf.open(destination) as check:
        if action == "lock":
            if not check.needs_pass or not check.authenticate(password):
                raise ValueError("Encryption verification failed")
        elif check.needs_pass or (check.metadata or {}).get("encryption"):
            raise ValueError("Decryption verification failed")
        if check.page_count != count:
            raise ValueError("Page count changed")


if __name__ == "__main__":
    try:
        payload = json.loads(sys.stdin.buffer.read(2048))
        secure(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], payload["password"])
    except PasswordError:
        sys.exit(3)
    except AlreadyProtected:
        sys.exit(4)
    except NotProtected:
        sys.exit(5)
    except Exception:
        sys.exit(2)
