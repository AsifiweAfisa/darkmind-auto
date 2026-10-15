"""
check_tokens.py — Show what's stored in the `tokens` table, WITHOUT printing
any secret values. Safe to paste the output into a chat.

Usage:
    python check_tokens.py
"""

from database import init_db, get_token

PLATFORMS = ["youtube", "tiktok", "instagram", "threads", "facebook"]


def main() -> None:
    init_db()
    print(f"{'platform':<11} {'access':<8} {'refresh':<8} {'expires_at (UTC)':<28} updated_at")
    print("-" * 80)
    for p in PLATFORMS:
        row = get_token(p)
        if not row:
            print(f"{p:<11} {'MISSING':<8}")
            continue
        has_access = "yes" if row.get("access_token") else "no"
        has_refresh = "yes" if row.get("refresh_token") else "-"
        expires = row.get("expires_at") or "(none/never or unknown)"
        print(f"{p:<11} {has_access:<8} {has_refresh:<8} {expires:<28} {row.get('updated_at')}")


if __name__ == "__main__":
    main()
