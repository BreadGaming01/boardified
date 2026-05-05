import os
import re
import uuid
import json
import hashlib
import requests
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask, session, redirect, request,
    render_template, url_for, flash, jsonify
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or "super_secret_dev_key_change_in_prod_xK9mP2qL"
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB upload limit

def get_cfb_base_url():
    return (os.environ.get("CFB_BASE_URL") or "").strip()

def get_cfb_api_key():
    return (os.environ.get("CFB_API_KEY") or "").strip()

DB_FILE = "db.json"
UPLOAD_FOLDER = os.path.join("static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_VIDEO = {"mp4", "webm", "mov", "avi", "mkv"}
ALLOWED_PDF   = {"pdf"}
ALLOWED_PHOTO = {"png", "jpg", "jpeg", "gif", "webp"}

def allowed_file(filename, allowed):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed

def extract_youtube_id(url):
    match = re.search(r'youtu\.be/([^?&\s]+)', url)
    if match: return match.group(1)
    match = re.search(r'[?&]v=([^?&\s]+)', url)
    if match: return match.group(1)
    match = re.search(r'youtube\.com/live/([^?&\s]+)', url)
    if match: return match.group(1)
    match = re.search(r'youtube\.com/embed/([^?&\s]+)', url)
    if match: return match.group(1)
    return None


# ─── Database Helpers ────────────────────────────────────────────────────────

def load_db():
    if not os.path.exists(DB_FILE):
        default = {"users": [], "courses": [], "purchases": [], "folders": [], "content_items": [], "coupons": []}
        save_db(default)
        return default
    with open(DB_FILE, "r") as f:
        db = json.load(f)
    if "folders" not in db: db["folders"] = []
    if "content_items" not in db: db["content_items"] = []
    if "coupons" not in db: db["coupons"] = []
    return db

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def now():
    return datetime.utcnow().isoformat()

def find_coupon(db, code):
    code = code.strip().upper()
    return next((c for c in db["coupons"] if c["code"].upper() == code), None)

def calc_discount(subtotal, coupon):
    if not coupon or not coupon.get("active", True):
        return 0.0
    if coupon["limit"] is not None and coupon["usage_count"] >= coupon["limit"]:
        return 0.0
    if coupon["type"] == "percent":
        return round(subtotal * coupon["value"] / 100, 2)
    return round(min(coupon["value"], subtotal), 2)

def ensure_default_admin():
    db = load_db()
    admin_email = "itzbreadgaming@gmail.com"
    admin_password = hash_password("tejas@2010")
    existing = next((u for u in db["users"] if u["email"] == admin_email), None)
    if existing:
        if existing["role"] != "admin":
            existing["role"] = "admin"
            save_db(db)
        if existing["password"] != admin_password:
            existing["password"] = admin_password
            save_db(db)
    else:
        db["users"].append({
            "id": str(uuid.uuid4()),
            "username": "admin",
            "email": admin_email,
            "password": admin_password,
            "role": "admin",
            "created_at": now()
        })
        save_db(db)

ensure_default_admin()

app.jinja_env.filters['extract_yt_id'] = extract_youtube_id


# ─── Auth Decorators ─────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            flash("Admin access required.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


# ─── Auth Routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    db = load_db()
    courses = db["courses"]
    user_purchases = []
    if "user_id" in session:
        user_purchases = [p["course_id"] for p in db["purchases"]
                          if p["user_id"] == session["user_id"] and p["status"] == "paid"]
    return render_template("index.html", courses=courses, user_purchases=user_purchases)

@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("index"))
    if request.method == "POST":
        db = load_db()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not username or not email or not password:
            flash("All fields are required.", "danger")
            return render_template("register.html")
        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("register.html")
        if any(u["email"] == email for u in db["users"]):
            flash("An account with this email already exists.", "danger")
            return render_template("register.html")
        if any(u["username"] == username for u in db["users"]):
            flash("Username already taken.", "danger")
            return render_template("register.html")
        role = "user"

        user = {
            "id": str(uuid.uuid4()),
            "username": username,
            "email": email,
            "password": hash_password(password),
            "role": role,
            "created_at": now()
        }
        db["users"].append(user)
        save_db(db)
        flash("Account created! Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))
    if request.method == "POST":
        db = load_db()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = next((u for u in db["users"]
                     if u["email"] == email and u["password"] == hash_password(password)), None)
        if not user:
            flash("Invalid email or password.", "danger")
            return render_template("login.html")
        session.permanent = True
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = user["role"]
        flash(f"Welcome back, {user['username']}!", "success")
        if user["role"] == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
def account():
    if "user_id" not in session:
        return redirect(url_for("login"))
    db = load_db()
    user = next((u for u in db["users"] if u["id"] == session["user_id"]), None)
    if not user:
        session.clear()
        return redirect(url_for("login"))

    if request.method == "POST":
        new_username = request.form.get("username", "").strip()
        new_email = request.form.get("email", "").strip().lower()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        errors = []

        if not new_username:
            errors.append("Username cannot be empty.")
        if not new_email:
            errors.append("Email cannot be empty.")

        if new_username and new_username != user["username"]:
            if any(u["username"] == new_username and u["id"] != user["id"] for u in db["users"]):
                errors.append("That username is already taken.")

        if new_email and new_email != user["email"]:
            if any(u["email"] == new_email and u["id"] != user["id"] for u in db["users"]):
                errors.append("An account with that email already exists.")

        changing_password = bool(new_password or confirm_password)
        if changing_password:
            if not current_password:
                errors.append("Enter your current password to set a new one.")
            elif user["password"] != hash_password(current_password):
                errors.append("Current password is incorrect.")
            elif len(new_password) < 6:
                errors.append("New password must be at least 6 characters.")
            elif new_password != confirm_password:
                errors.append("New passwords do not match.")

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template("account.html", user=user)

        user["username"] = new_username
        user["email"] = new_email
        if changing_password and not errors:
            user["password"] = hash_password(new_password)

        save_db(db)
        session["username"] = new_username
        flash("Account details updated successfully.", "success")
        return redirect(url_for("account"))

    return render_template("account.html", user=user)


# ─── Course Routes (Public/User) ──────────────────────────────────────────────

@app.route("/course/<course_id>")
def course_detail(course_id):
    db = load_db()
    course = next((c for c in db["courses"] if c["id"] == course_id), None)
    if not course:
        flash("Course not found.", "danger")
        return redirect(url_for("index"))
    purchased = False
    if "user_id" in session:
        purchased = any(p["course_id"] == course_id and p["user_id"] == session["user_id"]
                        and p["status"] == "paid" for p in db["purchases"])
    is_admin = session.get("role") == "admin"
    lib_folders = []
    lib_uncategorized = []
    item_map = {}
    folder_map = {}
    if purchased or is_admin:
        folders = [f for f in db["folders"] if f.get("course_id") == course_id]
        all_items = db["content_items"]
        for f in folders:
            f["content"] = [i for i in all_items if i.get("folder_id") == f["id"]]
        lib_folders = folders
        lib_uncategorized = [i for i in all_items if not i.get("folder_id") and i.get("course_id") == course_id]
        course_folder_ids = {f["id"] for f in folders}
        item_map   = {i["id"]: i for i in all_items
                      if i.get("course_id") == course_id
                      or i.get("folder_id") in course_folder_ids}
        folder_map = {f["id"]: f for f in folders}
    return render_template("course_detail.html", course=course, purchased=purchased,
                           lib_folders=lib_folders, lib_uncategorized=lib_uncategorized,
                           item_map=item_map, folder_map=folder_map)

@app.route("/my-courses")
@login_required
def my_courses():
    db = load_db()
    purchased_ids = [p["course_id"] for p in db["purchases"]
                     if p["user_id"] == session["user_id"] and p["status"] == "paid"]
    courses = [c for c in db["courses"] if c["id"] in purchased_ids]
    return render_template("my_courses.html", courses=courses)


# ─── Payment Routes (CFB) ─────────────────────────────────────────────────────

@app.route("/checkout/<course_id>")
@login_required
def checkout(course_id):
    db = load_db()
    course = next((c for c in db["courses"] if c["id"] == course_id), None)
    if not course:
        flash("Course not found.", "danger")
        return redirect(url_for("index"))

    already = any(p["course_id"] == course_id and p["user_id"] == session["user_id"]
                  and p["status"] == "paid" for p in db["purchases"])
    if already:
        flash("You already own this course.", "info")
        return redirect(url_for("my_courses"))

    order_id = str(uuid.uuid4())
    final_url = request.host_url.rstrip("/") + url_for("payment_done")

    purchase = {
        "id": order_id,
        "user_id": session["user_id"],
        "course_id": course_id,
        "amount": course["price"],
        "status": "pending",
        "session_key": order_id,
        "created_at": now()
    }
    db["purchases"].append(purchase)
    save_db(db)

    try:
        cfb_url = get_cfb_base_url()
        if not cfb_url:
            raise ValueError("CFB_BASE_URL not configured")
        resp = requests.get(f"{cfb_url}/api/pay/initiate", params={
            "token": get_cfb_api_key(),
            "requiredamount": course["price"],
            "session_key": order_id,
            "final_url": final_url
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        session["pending_order"] = {"session_key": order_id, "course_id": course_id}
        return redirect(data["payment_url"])
    except Exception as e:
        print(f"CFB Error: {e}")
        db = load_db()
        for p in db["purchases"]:
            if p["id"] == order_id:
                p["status"] = "failed"
        save_db(db)
        flash("Payment gateway unavailable. Please try again later.", "danger")
        return redirect(url_for("course_detail", course_id=course_id))

@app.route("/payment-done")
def payment_done():
    status = request.args.get("status")
    session_key = request.args.get("session_key")
    pending = session.get("pending_order", {})

    if status != "success" or session_key != pending.get("session_key"):
        flash("Payment failed or was cancelled.", "danger")
        return redirect(url_for("index"))

    try:
        cfb_url = get_cfb_base_url()
        if not cfb_url:
            raise ValueError("CFB_BASE_URL not configured")
        verify = requests.get(f"{cfb_url}/api/pay/verify", params={
            "token": get_cfb_api_key(),
            "session_key": session_key
        }, timeout=10)
        verify.raise_for_status()
        verify_data = verify.json()

        if verify_data.get("status") == "paid":
            db = load_db()
            for p in db["purchases"]:
                if p["session_key"] == session_key:
                    p["status"] = "paid"
            # increment coupon usage if cart order used one
            coupon_code = pending.get("coupon_code")
            if coupon_code:
                for c in db["coupons"]:
                    if c["code"].upper() == coupon_code.upper():
                        c["usage_count"] = c.get("usage_count", 0) + 1
            save_db(db)
            session.pop("pending_order", None)
            session.pop("cart", None)
            session.pop("cart_coupon", None)
            course_ids = pending.get("course_ids", [])
            course_id = pending.get("course_id") or (course_ids[0] if course_ids else None)
            course = next((c for c in db["courses"] if c["id"] == course_id), None)
            purchased_courses = [c for c in db["courses"] if c["id"] in course_ids] if course_ids else ([course] if course else [])
            return render_template("payment_success.html",
                                   course=course,
                                   courses=purchased_courses,
                                   amount=verify_data.get("amount"),
                                   session_key=session_key)
        else:
            flash("Payment not completed.", "danger")
            return redirect(url_for("index"))
    except Exception as e:
        print(f"Verification Error: {e}")
        flash("Could not verify payment. Contact support.", "danger")
        return redirect(url_for("index"))


# ─── Cart Routes ──────────────────────────────────────────────────────────────

@app.route("/cart")
@login_required
def cart():
    db = load_db()
    cart_ids = session.get("cart", [])
    owned_ids = {p["course_id"] for p in db["purchases"]
                 if p["user_id"] == session["user_id"] and p["status"] == "paid"}
    cart_ids = [cid for cid in cart_ids if cid not in owned_ids]
    session["cart"] = cart_ids
    courses = [c for c in db["courses"] if c["id"] in cart_ids]
    subtotal = sum(c["price"] for c in courses)

    coupon_code = session.get("cart_coupon", "")
    coupon = find_coupon(db, coupon_code) if coupon_code else None
    coupon_valid = coupon and coupon.get("active", True) and (
        coupon["limit"] is None or coupon["usage_count"] < coupon["limit"])
    if not coupon_valid:
        coupon = None
        session.pop("cart_coupon", None)
        coupon_code = ""

    discount = calc_discount(subtotal, coupon) if coupon else 0.0
    total = max(0.0, round(subtotal - discount, 2))
    return render_template("cart.html", courses=courses, subtotal=subtotal,
                           discount=discount, total=total,
                           coupon=coupon, coupon_code=coupon_code)

@app.route("/cart/add/<course_id>", methods=["POST"])
@login_required
def cart_add(course_id):
    db = load_db()
    course = next((c for c in db["courses"] if c["id"] == course_id), None)
    if not course:
        return jsonify({"ok": False, "msg": "Course not found"}), 404
    already_owned = any(p["course_id"] == course_id and p["user_id"] == session["user_id"]
                        and p["status"] == "paid" for p in db["purchases"])
    if already_owned:
        return jsonify({"ok": False, "msg": "You already own this course"}), 400
    cart_ids = session.get("cart", [])
    if course_id not in cart_ids:
        cart_ids.append(course_id)
    session["cart"] = cart_ids
    return jsonify({"ok": True, "count": len(cart_ids)})

@app.route("/cart/remove/<course_id>", methods=["POST"])
@login_required
def cart_remove(course_id):
    cart_ids = session.get("cart", [])
    cart_ids = [cid for cid in cart_ids if cid != course_id]
    session["cart"] = cart_ids
    return jsonify({"ok": True, "count": len(cart_ids)})

@app.route("/cart/apply-coupon", methods=["POST"])
@login_required
def cart_apply_coupon():
    code = request.form.get("coupon_code", "").strip().upper()
    if not code:
        session.pop("cart_coupon", None)
        flash("Coupon removed.", "info")
        return redirect(url_for("cart"))
    db = load_db()
    coupon = find_coupon(db, code)
    if not coupon:
        flash("Invalid coupon code.", "danger")
        return redirect(url_for("cart"))
    if not coupon.get("active", True):
        flash("This coupon is no longer active.", "danger")
        return redirect(url_for("cart"))
    if coupon["limit"] is not None and coupon["usage_count"] >= coupon["limit"]:
        flash("This coupon has reached its usage limit.", "danger")
        return redirect(url_for("cart"))
    session["cart_coupon"] = code
    flash(f"Coupon {code} applied!", "success")
    return redirect(url_for("cart"))

@app.route("/cart/checkout", methods=["POST"])
@login_required
def cart_checkout():
    db = load_db()
    cart_ids = session.get("cart", [])
    if not cart_ids:
        flash("Your cart is empty.", "warning")
        return redirect(url_for("cart"))

    owned_ids = {p["course_id"] for p in db["purchases"]
                 if p["user_id"] == session["user_id"] and p["status"] == "paid"}
    cart_ids = [cid for cid in cart_ids if cid not in owned_ids]
    if not cart_ids:
        session["cart"] = []
        flash("You already own all courses in your cart.", "info")
        return redirect(url_for("my_courses"))

    courses = [c for c in db["courses"] if c["id"] in cart_ids]
    subtotal = sum(c["price"] for c in courses)

    coupon_code = session.get("cart_coupon", "")
    coupon = find_coupon(db, coupon_code) if coupon_code else None
    coupon_valid = coupon and coupon.get("active", True) and (
        coupon["limit"] is None or coupon["usage_count"] < coupon["limit"])
    if not coupon_valid:
        coupon = None
    discount = calc_discount(subtotal, coupon) if coupon else 0.0
    total = max(0.0, round(subtotal - discount, 2))

    order_id = str(uuid.uuid4())
    for course in courses:
        db["purchases"].append({
            "id": str(uuid.uuid4()),
            "user_id": session["user_id"],
            "course_id": course["id"],
            "amount": course["price"],
            "final_amount": round(course["price"] * (total / subtotal) if subtotal > 0 else 0, 2),
            "coupon_code": coupon["code"] if coupon else None,
            "status": "pending",
            "session_key": order_id,
            "created_at": now()
        })

    if total <= 0:
        for p in db["purchases"]:
            if p["session_key"] == order_id:
                p["status"] = "paid"
        if coupon:
            for c in db["coupons"]:
                if c["code"].upper() == coupon["code"].upper():
                    c["usage_count"] = c.get("usage_count", 0) + 1
        save_db(db)
        session["cart"] = []
        session.pop("cart_coupon", None)
        purchased_courses = courses
        flash(f"Successfully enrolled in {len(courses)} course(s) for free!", "success")
        return render_template("payment_success.html",
                               course=courses[0] if courses else None,
                               courses=purchased_courses,
                               amount=0,
                               session_key=order_id)

    save_db(db)
    final_url = request.host_url.rstrip("/") + url_for("payment_done")
    try:
        cfb_url = get_cfb_base_url()
        if not cfb_url:
            raise ValueError("CFB_BASE_URL not configured")
        resp = requests.get(f"{cfb_url}/api/pay/initiate", params={
            "token": get_cfb_api_key(),
            "requiredamount": total,
            "session_key": order_id,
            "final_url": final_url
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        session["pending_order"] = {
            "session_key": order_id,
            "course_ids": cart_ids,
            "course_id": cart_ids[0],
            "coupon_code": coupon["code"] if coupon else None
        }
        return redirect(data["payment_url"])
    except Exception as e:
        print(f"CFB Error: {e}")
        db = load_db()
        for p in db["purchases"]:
            if p["session_key"] == order_id:
                p["status"] = "failed"
        save_db(db)
        flash("Payment gateway unavailable. Please try again later.", "danger")
        return redirect(url_for("cart"))


# ─── Admin Routes ─────────────────────────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin_dashboard():
    db = load_db()
    courses = db["courses"]
    users = db["users"]
    purchases = [p for p in db["purchases"] if p["status"] == "paid"]
    total_revenue = sum(p["amount"] for p in purchases)
    return render_template("admin/dashboard.html",
                           courses=courses,
                           users=users,
                           purchases=purchases,
                           total_revenue=total_revenue)

@app.route("/admin/users/<user_id>")
@admin_required
def admin_user_detail(user_id):
    db = load_db()
    user = next((u for u in db["users"] if u["id"] == user_id), None)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin_dashboard"))
    user_purchases = [p for p in db["purchases"] if p["user_id"] == user_id and p["status"] == "paid"]
    course_map = {c["id"]: c for c in db["courses"]}
    enrollments = []
    for p in user_purchases:
        course = course_map.get(p["course_id"])
        if course:
            enrollments.append({"purchase": p, "course": course})
    enrolled_ids = {e["purchase"]["course_id"] for e in enrollments}
    available_courses = [c for c in db["courses"] if c["id"] not in enrolled_ids]
    return render_template("admin/user_detail.html", user=user, enrollments=enrollments,
                           available_courses=available_courses)

@app.route("/admin/purchases/<purchase_id>/revoke", methods=["POST"])
@admin_required
def admin_revoke_purchase(purchase_id):
    db = load_db()
    purchase = next((p for p in db["purchases"] if p["id"] == purchase_id), None)
    if not purchase:
        flash("Purchase not found.", "danger")
        return redirect(url_for("admin_dashboard"))
    user_id = purchase.get("user_id")
    db["purchases"] = [p for p in db["purchases"] if p["id"] != purchase_id]
    save_db(db)
    flash("Access revoked successfully.", "success")
    if user_id:
        return redirect(url_for("admin_user_detail", user_id=user_id))
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/users/<user_id>/grant-access", methods=["POST"])
@admin_required
def admin_grant_access(user_id):
    db = load_db()
    user = next((u for u in db["users"] if u["id"] == user_id), None)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin_dashboard"))
    course_id = request.form.get("course_id", "").strip()
    if not course_id:
        flash("Please select a course.", "danger")
        return redirect(url_for("admin_user_detail", user_id=user_id))
    course = next((c for c in db["courses"] if c["id"] == course_id), None)
    if not course:
        flash("Course not found.", "danger")
        return redirect(url_for("admin_user_detail", user_id=user_id))
    already = any(p["course_id"] == course_id and p["user_id"] == user_id
                  and p["status"] == "paid" for p in db["purchases"])
    if already:
        flash(f"{user['username']} already has access to this course.", "info")
        return redirect(url_for("admin_user_detail", user_id=user_id))
    purchase = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "course_id": course_id,
        "amount": 0,
        "status": "paid",
        "session_key": "admin_grant",
        "created_at": now()
    }
    db["purchases"].append(purchase)
    save_db(db)
    flash(f"Access to \"{course['title']}\" granted to {user['username']}.", "success")
    return redirect(url_for("admin_user_detail", user_id=user_id))

@app.route("/admin/courses/new", methods=["GET", "POST"])
@admin_required
def admin_create_course():
    if request.method == "POST":
        db = load_db()
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        price = request.form.get("price", "0")
        category = request.form.get("category", "").strip()
        thumbnail = request.form.get("thumbnail", "").strip()
        level = request.form.get("level", "Beginner")

        if not title or not description:
            flash("Title and description are required.", "danger")
            return render_template("admin/course_form.html", course=None, action="Create")
        try:
            price = float(price)
            if price < 0:
                raise ValueError
        except ValueError:
            flash("Invalid price.", "danger")
            return render_template("admin/course_form.html", course=None, action="Create")

        course = {
            "id": str(uuid.uuid4()),
            "title": title,
            "description": description,
            "price": price,
            "category": category,
            "thumbnail": thumbnail or "",
            "level": level,
            "instructor": session["username"],
            "lessons": [],
            "created_at": now(),
            "updated_at": now()
        }
        db["courses"].append(course)
        save_db(db)
        flash(f'Course "{title}" created successfully!', "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("admin/course_form.html", course=None, action="Create")

@app.route("/admin/courses/<course_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_course(course_id):
    db = load_db()
    course = next((c for c in db["courses"] if c["id"] == course_id), None)
    if not course:
        flash("Course not found.", "danger")
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        price = request.form.get("price", "0")
        category = request.form.get("category", "").strip()
        thumbnail = request.form.get("thumbnail", "").strip()
        level = request.form.get("level", "Beginner")

        if not title or not description:
            flash("Title and description are required.", "danger")
            return render_template("admin/course_form.html", course=course, action="Save Changes")
        try:
            price = float(price)
            if price < 0:
                raise ValueError
        except ValueError:
            flash("Invalid price.", "danger")
            return render_template("admin/course_form.html", course=course, action="Save Changes")

        for c in db["courses"]:
            if c["id"] == course_id:
                c["title"] = title
                c["description"] = description
                c["price"] = price
                c["category"] = category
                c["thumbnail"] = thumbnail
                c["level"] = level
                c["updated_at"] = now()
        save_db(db)
        flash(f'Course "{title}" updated successfully!', "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("admin/course_form.html", course=course, action="Save Changes")

@app.route("/admin/courses/<course_id>/delete", methods=["POST"])
@admin_required
def admin_delete_course(course_id):
    db = load_db()
    db["courses"] = [c for c in db["courses"] if c["id"] != course_id]
    save_db(db)
    flash("Course deleted.", "info")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/courses/<course_id>/lessons", methods=["GET", "POST"])
@admin_required
def admin_lessons(course_id):
    db = load_db()
    course = next((c for c in db["courses"] if c["id"] == course_id), None)
    if not course:
        flash("Course not found.", "danger")
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            lesson_title = request.form.get("lesson_title", "").strip()
            lesson_content = request.form.get("lesson_content", "").strip()
            lesson_video = request.form.get("lesson_video", "").strip()
            if lesson_title:
                lesson = {
                    "id": str(uuid.uuid4()),
                    "title": lesson_title,
                    "content": lesson_content,
                    "video_url": lesson_video,
                    "order": len(course["lessons"]) + 1
                }
                for c in db["courses"]:
                    if c["id"] == course_id:
                        c["lessons"].append(lesson)
                        c["updated_at"] = now()
                save_db(db)
                flash("Lesson added.", "success")
        elif action == "delete":
            lesson_id = request.form.get("lesson_id")
            for c in db["courses"]:
                if c["id"] == course_id:
                    c["lessons"] = [l for l in c["lessons"] if l["id"] != lesson_id]
                    c["updated_at"] = now()
            save_db(db)
            flash("Lesson removed.", "info")
        db = load_db()
        course = next((c for c in db["courses"] if c["id"] == course_id), None)

    # Load library folders and items for this course (for attachment picker)
    lib_folders = [f for f in db["folders"] if f.get("course_id") == course_id]
    lib_items   = [i for i in db["content_items"] if i.get("course_id") == course_id]
    # Build a lookup for quick display in template
    item_map  = {i["id"]: i for i in db["content_items"]}
    folder_map = {f["id"]: f for f in db["folders"]}
    return render_template("admin/lessons.html", course=course,
                           lib_folders=lib_folders, lib_items=lib_items,
                           item_map=item_map, folder_map=folder_map)


@app.route("/admin/courses/<course_id>/lessons/<lesson_id>/attachments", methods=["POST"])
@admin_required
def admin_lesson_attachments(course_id, lesson_id):
    db = load_db()
    action    = request.form.get("action")
    item_id   = request.form.get("item_id")
    folder_id = request.form.get("folder_id")
    for c in db["courses"]:
        if c["id"] != course_id:
            continue
        for lesson in c["lessons"]:
            if lesson["id"] != lesson_id:
                continue
            att = lesson.setdefault("attachments", {"item_ids": [], "folder_ids": []})
            att.setdefault("item_ids", [])
            att.setdefault("folder_ids", [])
            if action == "attach_item" and item_id and item_id not in att["item_ids"]:
                att["item_ids"].append(item_id)
            elif action == "detach_item" and item_id and item_id in att["item_ids"]:
                att["item_ids"].remove(item_id)
            elif action == "attach_folder" and folder_id and folder_id not in att["folder_ids"]:
                att["folder_ids"].append(folder_id)
            elif action == "detach_folder" and folder_id and folder_id in att["folder_ids"]:
                att["folder_ids"].remove(folder_id)
            c["updated_at"] = now()
    save_db(db)
    return redirect(url_for("admin_lessons", course_id=course_id) + f"#lesson-{lesson_id}")


# ─── Content Library Routes ───────────────────────────────────────────────────

@app.route("/admin/library")
@admin_required
def admin_library():
    db = load_db()
    folders = [f for f in db["folders"] if not f.get("course_id") and not f.get("parent_folder_id")]
    items = db["content_items"]
    for f in folders:
        f["item_count"] = sum(1 for i in items if i.get("folder_id") == f["id"])
    uncategorized = [i for i in items if not i.get("folder_id") and not i.get("course_id")]
    return render_template("admin/library.html",
                           folders=folders,
                           uncategorized=uncategorized,
                           total_items=len(items))

@app.route("/admin/library/folder/new", methods=["POST"])
@admin_required
def admin_create_folder():
    db = load_db()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    color = request.form.get("color", "#6366f1").strip()
    icon = request.form.get("icon", "📁").strip()
    parent_folder_id = request.form.get("parent_folder_id", "").strip() or None
    if not name:
        flash("Folder name is required.", "danger")
        if parent_folder_id:
            return redirect(url_for("admin_folder", folder_id=parent_folder_id))
        return redirect(url_for("admin_library"))
    db["folders"].append({
        "id": str(uuid.uuid4()),
        "name": name,
        "description": description,
        "color": color,
        "icon": icon or "📁",
        "parent_folder_id": parent_folder_id,
        "created_at": now()
    })
    save_db(db)
    flash(f'Folder "{name}" created.', "success")
    if parent_folder_id:
        return redirect(url_for("admin_folder", folder_id=parent_folder_id))
    return redirect(url_for("admin_library"))

@app.route("/admin/library/folder/<folder_id>")
@admin_required
def admin_folder(folder_id):
    db = load_db()
    folder = next((f for f in db["folders"] if f["id"] == folder_id), None)
    if not folder:
        flash("Folder not found.", "danger")
        return redirect(url_for("admin_library"))
    items = [i for i in db["content_items"] if i.get("folder_id") == folder_id]
    subfolders = [f for f in db["folders"] if f.get("parent_folder_id") == folder_id]
    for sf in subfolders:
        sf["item_count"] = sum(1 for i in db["content_items"] if i.get("folder_id") == sf["id"])
    parent = next((f for f in db["folders"] if f["id"] == folder.get("parent_folder_id")), None) if folder.get("parent_folder_id") else None
    all_folders = [f for f in db["folders"] if f["id"] != folder_id and not f.get("course_id")]
    return render_template("admin/folder.html", folder=folder, items=items,
                           subfolders=subfolders, parent=parent, all_folders=all_folders)

@app.route("/admin/library/folder/<folder_id>/delete", methods=["POST"])
@admin_required
def admin_delete_folder(folder_id):
    db = load_db()
    folder = next((f for f in db["folders"] if f["id"] == folder_id), None)
    parent_folder_id = folder.get("parent_folder_id") if folder else None
    child_folder_ids = [f["id"] for f in db["folders"] if f.get("parent_folder_id") == folder_id]
    for item in db["content_items"]:
        if item.get("folder_id") == folder_id or item.get("folder_id") in child_folder_ids:
            item["folder_id"] = None
    db["folders"] = [f for f in db["folders"] if f["id"] != folder_id and f.get("parent_folder_id") != folder_id]
    save_db(db)
    flash("Folder deleted. Its contents were moved to Uncategorized.", "info")
    if parent_folder_id:
        return redirect(url_for("admin_folder", folder_id=parent_folder_id))
    return redirect(url_for("admin_library"))

@app.route("/admin/library/folder/<folder_id>/rename", methods=["POST"])
@admin_required
def admin_rename_folder(folder_id):
    db = load_db()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    color = request.form.get("color", "#6366f1").strip()
    icon = request.form.get("icon", "📁").strip()
    for f in db["folders"]:
        if f["id"] == folder_id:
            if name: f["name"] = name
            f["description"] = description
            f["color"] = color
            f["icon"] = icon or "📁"
    save_db(db)
    flash("Folder updated.", "success")
    return redirect(url_for("admin_folder", folder_id=folder_id))

@app.route("/admin/library/content/add", methods=["POST"])
@admin_required
def admin_add_content():
    db = load_db()
    title       = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    ctype       = request.form.get("type", "link").strip()
    folder_id   = request.form.get("folder_id", "").strip() or None
    url_input   = next((u.strip() for u in request.form.getlist("url") if u.strip()), "")

    back = url_for("admin_folder", folder_id=folder_id) if folder_id else url_for("admin_library")

    if not title:
        flash("Title is required.", "danger")
        return redirect(back)

    if ctype in ("youtube", "youtube_live", "link") and not url_input:
        flash("A URL is required for this content type.", "danger")
        return redirect(back)

    file_path = None
    file_name = None
    if ctype in ("video", "pdf", "photo"):
        all_files = request.files.getlist("file")
        f = next((fi for fi in all_files if fi and fi.filename), None)
        if f and f.filename:
            ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
            allowed_map = {"video": ALLOWED_VIDEO, "pdf": ALLOWED_PDF, "photo": ALLOWED_PHOTO}
            if ext not in allowed_map[ctype]:
                flash(f"Invalid file type for {ctype}. Allowed: {', '.join(allowed_map[ctype])}", "danger")
                return redirect(back)
            unique_name = f"{uuid.uuid4()}_{secure_filename(f.filename)}"
            f.save(os.path.join(UPLOAD_FOLDER, unique_name))
            file_path = f"uploads/{unique_name}"
            file_name = f.filename
        elif url_input and ctype == "video":
            pass  # allow URL fallback for video
        elif ctype == "video":
            flash("Please provide a video file or a URL.", "danger")
            return redirect(back)
        else:
            flash(f"Please provide a {'PDF' if ctype == 'pdf' else 'photo'} file.", "danger")
            return redirect(back)

    db["content_items"].append({
        "id": str(uuid.uuid4()),
        "folder_id": folder_id,
        "title": title,
        "description": description,
        "type": ctype,
        "url": url_input or None,
        "file_path": file_path,
        "file_name": file_name,
        "created_at": now()
    })
    save_db(db)
    flash(f'"{title}" added.', "success")
    return redirect(back)

@app.route("/admin/library/content/<item_id>/delete", methods=["POST"])
@admin_required
def admin_delete_content(item_id):
    db = load_db()
    item = next((i for i in db["content_items"] if i["id"] == item_id), None)
    folder_id = item.get("folder_id") if item else None
    if item and item.get("file_path"):
        full = os.path.join("static", item["file_path"])
        if os.path.exists(full):
            os.remove(full)
    db["content_items"] = [i for i in db["content_items"] if i["id"] != item_id]
    save_db(db)
    flash("Content deleted.", "info")
    back = url_for("admin_folder", folder_id=folder_id) if folder_id else url_for("admin_library")
    return redirect(back)

@app.route("/admin/library/content/<item_id>/move", methods=["POST"])
@admin_required
def admin_move_content(item_id):
    db = load_db()
    new_folder_id = request.form.get("folder_id", "").strip() or None
    old_folder_id = None
    for item in db["content_items"]:
        if item["id"] == item_id:
            old_folder_id = item.get("folder_id")
            item["folder_id"] = new_folder_id
    save_db(db)
    flash("Content moved.", "success")
    back = url_for("admin_folder", folder_id=new_folder_id) if new_folder_id else url_for("admin_library")
    return redirect(back)


# ─── Course Content Library Routes ────────────────────────────────────────────

@app.route("/admin/courses/<course_id>/library")
@admin_required
def admin_course_library(course_id):
    db = load_db()
    course = next((c for c in db["courses"] if c["id"] == course_id), None)
    if not course:
        flash("Course not found.", "danger")
        return redirect(url_for("admin_dashboard"))
    folders = [f for f in db["folders"] if f.get("course_id") == course_id and not f.get("parent_folder_id")]
    items = db["content_items"]
    for f in folders:
        f["item_count"] = sum(1 for i in items if i.get("folder_id") == f["id"])
    uncategorized = [i for i in items if not i.get("folder_id") and i.get("course_id") == course_id]
    total_items = sum(f["item_count"] for f in folders) + len(uncategorized)
    return render_template("admin/course_library.html",
                           course=course, folders=folders,
                           uncategorized=uncategorized, total_items=total_items)

@app.route("/admin/courses/<course_id>/library/folder/new", methods=["POST"])
@admin_required
def admin_course_create_folder(course_id):
    db = load_db()
    course = next((c for c in db["courses"] if c["id"] == course_id), None)
    if not course:
        return redirect(url_for("admin_dashboard"))
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    color = request.form.get("color", "#6366f1").strip()
    icon = request.form.get("icon", "📁").strip()
    parent_folder_id = request.form.get("parent_folder_id", "").strip() or None
    if not name:
        flash("Folder name is required.", "danger")
        if parent_folder_id:
            return redirect(url_for("admin_course_folder", course_id=course_id, folder_id=parent_folder_id))
        return redirect(url_for("admin_course_library", course_id=course_id))
    db["folders"].append({
        "id": str(uuid.uuid4()),
        "name": name,
        "description": description,
        "color": color,
        "icon": icon or "📁",
        "course_id": course_id,
        "parent_folder_id": parent_folder_id,
        "created_at": now()
    })
    save_db(db)
    flash(f'Folder "{name}" created.', "success")
    if parent_folder_id:
        return redirect(url_for("admin_course_folder", course_id=course_id, folder_id=parent_folder_id))
    return redirect(url_for("admin_course_library", course_id=course_id))

@app.route("/admin/courses/<course_id>/library/folder/<folder_id>")
@admin_required
def admin_course_folder(course_id, folder_id):
    db = load_db()
    course = next((c for c in db["courses"] if c["id"] == course_id), None)
    folder = next((f for f in db["folders"] if f["id"] == folder_id and f.get("course_id") == course_id), None)
    if not course or not folder:
        flash("Not found.", "danger")
        return redirect(url_for("admin_course_library", course_id=course_id))
    items = [i for i in db["content_items"] if i.get("folder_id") == folder_id]
    subfolders = [f for f in db["folders"] if f.get("parent_folder_id") == folder_id and f.get("course_id") == course_id]
    for sf in subfolders:
        sf["item_count"] = sum(1 for i in db["content_items"] if i.get("folder_id") == sf["id"])
    parent = next((f for f in db["folders"] if f["id"] == folder.get("parent_folder_id")), None) if folder.get("parent_folder_id") else None
    all_folders = [f for f in db["folders"] if f.get("course_id") == course_id and f["id"] != folder_id]
    return render_template("admin/course_folder.html",
                           course=course, folder=folder, items=items,
                           subfolders=subfolders, parent=parent, all_folders=all_folders)

@app.route("/admin/courses/<course_id>/library/folder/<folder_id>/delete", methods=["POST"])
@admin_required
def admin_course_delete_folder(course_id, folder_id):
    db = load_db()
    folder = next((f for f in db["folders"] if f["id"] == folder_id), None)
    parent_folder_id = folder.get("parent_folder_id") if folder else None
    child_folder_ids = [f["id"] for f in db["folders"] if f.get("parent_folder_id") == folder_id]
    for item in db["content_items"]:
        if item.get("folder_id") == folder_id or item.get("folder_id") in child_folder_ids:
            item["folder_id"] = None
            item["course_id"] = course_id
    db["folders"] = [f for f in db["folders"] if f["id"] != folder_id and f.get("parent_folder_id") != folder_id]
    save_db(db)
    flash("Folder deleted. Contents moved to Uncategorized.", "info")
    if parent_folder_id:
        return redirect(url_for("admin_course_folder", course_id=course_id, folder_id=parent_folder_id))
    return redirect(url_for("admin_course_library", course_id=course_id))

@app.route("/admin/courses/<course_id>/library/folder/<folder_id>/rename", methods=["POST"])
@admin_required
def admin_course_rename_folder(course_id, folder_id):
    db = load_db()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    color = request.form.get("color", "#6366f1").strip()
    icon = request.form.get("icon", "📁").strip()
    for f in db["folders"]:
        if f["id"] == folder_id and f.get("course_id") == course_id:
            if name: f["name"] = name
            f["description"] = description
            f["color"] = color
            f["icon"] = icon or "📁"
    save_db(db)
    flash("Folder updated.", "success")
    return redirect(url_for("admin_course_folder", course_id=course_id, folder_id=folder_id))

@app.route("/admin/courses/<course_id>/library/content/add", methods=["POST"])
@admin_required
def admin_course_add_content(course_id):
    db = load_db()
    course = next((c for c in db["courses"] if c["id"] == course_id), None)
    if not course:
        return redirect(url_for("admin_dashboard"))
    title       = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    ctype       = request.form.get("type", "link").strip()
    folder_id   = request.form.get("folder_id", "").strip() or None
    url_input   = next((u.strip() for u in request.form.getlist("url") if u.strip()), "")

    back = (url_for("admin_course_folder", course_id=course_id, folder_id=folder_id)
            if folder_id else url_for("admin_course_library", course_id=course_id))

    if not title:
        flash("Title is required.", "danger")
        return redirect(back)

    if ctype in ("youtube", "youtube_live", "link") and not url_input:
        flash("A URL is required for this content type.", "danger")
        return redirect(back)

    file_path = None
    file_name = None
    if ctype in ("video", "pdf", "photo"):
        all_files = request.files.getlist("file")
        f = next((fi for fi in all_files if fi and fi.filename), None)
        if f and f.filename:
            ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
            allowed_map = {"video": ALLOWED_VIDEO, "pdf": ALLOWED_PDF, "photo": ALLOWED_PHOTO}
            if ext not in allowed_map[ctype]:
                flash(f"Invalid file type for {ctype}. Allowed: {', '.join(allowed_map[ctype])}", "danger")
                return redirect(back)
            unique_name = f"{uuid.uuid4()}_{secure_filename(f.filename)}"
            f.save(os.path.join(UPLOAD_FOLDER, unique_name))
            file_path = f"uploads/{unique_name}"
            file_name = f.filename
        elif url_input and ctype == "video":
            pass  # allow URL fallback for video
        elif ctype == "video":
            flash("Please provide a video file or a URL.", "danger")
            return redirect(back)
        else:
            flash(f"Please provide a {'PDF' if ctype == 'pdf' else 'photo'} file.", "danger")
            return redirect(back)

    db["content_items"].append({
        "id": str(uuid.uuid4()),
        "folder_id": folder_id,
        "course_id": course_id,
        "title": title,
        "description": description,
        "type": ctype,
        "url": url_input or None,
        "file_path": file_path,
        "file_name": file_name,
        "created_at": now()
    })
    save_db(db)
    flash(f'"{title}" added.', "success")
    return redirect(back)

@app.route("/admin/courses/<course_id>/library/content/<item_id>/delete", methods=["POST"])
@admin_required
def admin_course_delete_content(course_id, item_id):
    db = load_db()
    item = next((i for i in db["content_items"] if i["id"] == item_id), None)
    folder_id = item.get("folder_id") if item else None
    if item and item.get("file_path"):
        full = os.path.join("static", item["file_path"])
        if os.path.exists(full): os.remove(full)
    db["content_items"] = [i for i in db["content_items"] if i["id"] != item_id]
    save_db(db)
    flash("Content deleted.", "info")
    back = (url_for("admin_course_folder", course_id=course_id, folder_id=folder_id)
            if folder_id else url_for("admin_course_library", course_id=course_id))
    return redirect(back)

@app.route("/admin/courses/<course_id>/library/content/<item_id>/move", methods=["POST"])
@admin_required
def admin_course_move_content(course_id, item_id):
    db = load_db()
    new_folder_id = request.form.get("folder_id", "").strip() or None
    old_folder_id = None
    for item in db["content_items"]:
        if item["id"] == item_id:
            old_folder_id = item.get("folder_id")
            item["folder_id"] = new_folder_id
    save_db(db)
    flash("Content moved.", "success")
    back = (url_for("admin_course_folder", course_id=course_id, folder_id=new_folder_id)
            if new_folder_id else url_for("admin_course_library", course_id=course_id))
    return redirect(back)


# ─── Admin Coupon Routes ──────────────────────────────────────────────────────

@app.route("/admin/coupons", methods=["GET", "POST"])
@admin_required
def admin_coupons():
    db = load_db()
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        ctype = request.form.get("type", "percent")
        value_str = request.form.get("value", "0").strip()
        limit_str = request.form.get("limit", "").strip()

        if not code:
            flash("Coupon code is required.", "danger")
            return redirect(url_for("admin_coupons"))
        if any(c["code"].upper() == code for c in db["coupons"]):
            flash("A coupon with that code already exists.", "danger")
            return redirect(url_for("admin_coupons"))
        try:
            value = float(value_str)
            if value < 0: raise ValueError
            if ctype == "percent" and value > 100: raise ValueError
        except ValueError:
            flash("Invalid discount value.", "danger")
            return redirect(url_for("admin_coupons"))
        limit = None
        if limit_str:
            try:
                limit = int(limit_str)
                if limit < 1: raise ValueError
            except ValueError:
                flash("Usage limit must be a positive number (or leave blank for unlimited).", "danger")
                return redirect(url_for("admin_coupons"))
        db["coupons"].append({
            "id": str(uuid.uuid4()),
            "code": code,
            "type": ctype,
            "value": value,
            "limit": limit,
            "usage_count": 0,
            "active": True,
            "created_at": now()
        })
        save_db(db)
        flash(f"Coupon {code} created.", "success")
        return redirect(url_for("admin_coupons"))
    return render_template("admin/coupons.html", coupons=db["coupons"])

@app.route("/admin/coupons/<coupon_id>/edit", methods=["POST"])
@admin_required
def admin_coupon_edit(coupon_id):
    db = load_db()
    coupon = next((c for c in db["coupons"] if c["id"] == coupon_id), None)
    if not coupon:
        flash("Coupon not found.", "danger")
        return redirect(url_for("admin_coupons"))
    code = request.form.get("code", "").strip().upper()
    ctype = request.form.get("type", coupon["type"])
    value_str = request.form.get("value", str(coupon["value"])).strip()
    limit_str = request.form.get("limit", "").strip()

    if not code:
        flash("Coupon code is required.", "danger")
        return redirect(url_for("admin_coupons"))
    if any(c["code"].upper() == code and c["id"] != coupon_id for c in db["coupons"]):
        flash("Another coupon with that code already exists.", "danger")
        return redirect(url_for("admin_coupons"))
    try:
        value = float(value_str)
        if value < 0: raise ValueError
        if ctype == "percent" and value > 100: raise ValueError
    except ValueError:
        flash("Invalid discount value.", "danger")
        return redirect(url_for("admin_coupons"))
    limit = None
    if limit_str:
        try:
            limit = int(limit_str)
            if limit < 1: raise ValueError
        except ValueError:
            flash("Usage limit must be a positive number (or leave blank for unlimited).", "danger")
            return redirect(url_for("admin_coupons"))
    coupon["code"] = code
    coupon["type"] = ctype
    coupon["value"] = value
    coupon["limit"] = limit
    save_db(db)
    flash(f"Coupon {code} updated.", "success")
    return redirect(url_for("admin_coupons"))

@app.route("/admin/coupons/<coupon_id>/toggle", methods=["POST"])
@admin_required
def admin_coupon_toggle(coupon_id):
    db = load_db()
    coupon = next((c for c in db["coupons"] if c["id"] == coupon_id), None)
    if coupon:
        coupon["active"] = not coupon.get("active", True)
        save_db(db)
        state = "activated" if coupon["active"] else "deactivated"
        flash(f"Coupon {coupon['code']} {state}.", "success")
    return redirect(url_for("admin_coupons"))

@app.route("/admin/coupons/<coupon_id>/delete", methods=["POST"])
@admin_required
def admin_coupon_delete(coupon_id):
    db = load_db()
    db["coupons"] = [c for c in db["coupons"] if c["id"] != coupon_id]
    save_db(db)
    flash("Coupon deleted.", "success")
    return redirect(url_for("admin_coupons"))


@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(413)
def request_entity_too_large(e):
    flash("File is too large. Maximum upload size is 500 MB.", "danger")
    return redirect(request.referrer or url_for("admin_library"))

if __name__ == "__main__":
    app.run(port=6000)