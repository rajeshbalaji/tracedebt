"""Load generator that mirrors Online Boutique's locustfile (same tasks and
weights: index 1, setCurrency 2, browseProduct 10, addToCart 2, viewCart 3,
checkout 1) without needing Locust. Each simulated user keeps its own session
cookie and waits `--think` seconds (uniform) between tasks.

    python loadgen.py --url http://127.0.0.1:8080 --users 10 --seconds 120
"""
import argparse
import random
import threading
import time

import requests

PRODUCTS = ['0PUK6V6EV0', '1YMWWN1N4O', '2ZYFJ3GM2N', '66VCHSJNUP', '6E92ZMYYFZ',
            '9SIQT8TOJO', 'L9ECAV7KIM', 'LS4PSXUNUM', 'OLJCESPC7Z']


def index(s, u): s.get(u + "/")
def set_currency(s, u): s.post(u + "/setCurrency", data={"currency_code": random.choice(
    ['EUR', 'USD', 'JPY', 'CAD', 'GBP', 'TRY'])})
def browse(s, u): s.get(u + "/product/" + random.choice(PRODUCTS))
def view_cart(s, u): s.get(u + "/cart")


def add_to_cart(s, u):
    p = random.choice(PRODUCTS)
    s.get(u + "/product/" + p)
    s.post(u + "/cart", data={"product_id": p, "quantity": random.randint(1, 10)})


def checkout(s, u):
    add_to_cart(s, u)
    y = time.gmtime().tm_year + 1
    s.post(u + "/cart/checkout", data={
        "email": f"user{random.randint(1, 9999)}@example.com",
        "street_address": "1600 Amphitheatre Parkway", "zip_code": "94043",
        "city": "Mountain View", "state": "CA", "country": "United States",
        "credit_card_number": "4432801561520454",
        "credit_card_expiration_month": random.randint(1, 12),
        "credit_card_expiration_year": random.randint(y, y + 5),
        "credit_card_cvv": str(random.randint(100, 999))})


TASKS = [(index, 1), (set_currency, 2), (browse, 10), (add_to_cart, 2), (view_cart, 3), (checkout, 1)]
STATS = {"ok": 0, "err": 0}
LOCK = threading.Lock()


def user(url, stop, think, seed):
    rnd = random.Random(seed)
    s = requests.Session()
    try:
        index(s, url)
    except requests.RequestException:
        pass
    fns, ws = zip(*TASKS)
    while time.time() < stop:
        f = rnd.choices(fns, weights=ws)[0]
        try:
            f(s, url)
            with LOCK:
                STATS["ok"] += 1
        except requests.RequestException:
            with LOCK:
                STATS["err"] += 1
        time.sleep(rnd.uniform(*think))


def run(url, users, seconds, think=(0.5, 2.0), seed=0):
    random.seed(seed)
    stop = time.time() + seconds
    ts = [threading.Thread(target=user, args=(url, stop, think, seed * 1000 + i), daemon=True)
          for i in range(users)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return dict(STATS)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--users", type=int, default=10)
    ap.add_argument("--seconds", type=int, default=60)
    a = ap.parse_args()
    print(run(a.url, a.users, a.seconds))
