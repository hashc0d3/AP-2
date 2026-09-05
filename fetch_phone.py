"""Разовый тест номера: user session или spfa cookies."""

from __future__ import annotations

import sys

from loguru import logger

from avito_phone import fetch_phone
from avito_user import build_user_client, fetch_user_phone, load_user_session, session_status
from cookie_pool import buy_one, load_config, unblock_one, wait_ready_cookie
from parser import build_client, change_ip


def main() -> None:
    ad_id = sys.argv[1] if len(sys.argv) > 1 else "8272457720"
    use_user = "--user" in sys.argv or "-u" in sys.argv

    if use_user or session_status().get("connected"):
        result = fetch_user_phone(ad_id)
        if result.get("ok"):
            print(f"\nНомер: {result['phone']}\n")
        else:
            print(f"\n{result.get('error')} ({result.get('code')})\n")
        return

    cfg = load_config()
    try:
        change_ip(cfg["proxy_change_url"])
    except Exception as err:
        logger.warning(f"Смена IP: {err}")

    session = wait_ready_cookie(attempts=2)
    if session:
        session = unblock_one(session, cfg) or session
    if not session or not session.get("unblock_ok"):
        logger.info("Покупаю свежий mobile cookies на spfa.pro...")
        session = buy_one(cfg)
        unblock_one(session, cfg)

    client = build_client(session, cfg["proxy_string"])
    logger.info(f"Cookie id={session.get('id')}, ad={ad_id} (гостевая сессия)")
    result = fetch_phone(client, ad_id)
    if result.get("ok"):
        print(f"\nНомер: {result['phone']}\n")
    else:
        print(f"\n{result.get('error')} ({result.get('code')})\n")
        print("Для авторизованной сессии: python connect_avito.py, затем python fetch_phone.py AD_ID --user\n")


if __name__ == "__main__":
    main()
