import os
import json
import logging
import time
import re
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ==================== KONFIGURASI ====================
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ Token bot tidak ditemukan di .env")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== MUAT DATABASE ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(BASE_DIR, "kost_data.json")
PENGUSAHA_PATH = os.path.join(BASE_DIR, "pengusaha.json")
IMAGE_DIR = os.path.join(BASE_DIR, "images")
os.makedirs(IMAGE_DIR, exist_ok=True)

# --- Data Kos ---
try:
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        KOS_DATA = json.load(f)
    for kos in KOS_DATA:
        if "status" not in kos:
            kos["status"] = "approved"
        if isinstance(kos.get("gambar"), list):
            kos["gambar"] = kos["gambar"][0] if kos["gambar"] else ""
        elif not isinstance(kos.get("gambar"), str):
            kos["gambar"] = ""
        kos.setdefault("jenis_kos", "Tidak disebutkan")
        kos.setdefault("kamar_kosong", 0)
        kos.setdefault("deskripsi", "")
        kos.setdefault("gambar_lain", "")
        kos.setdefault("link_maps", "")
    logger.info(f"✅ Data kos dimuat: {len(KOS_DATA)} item")
except FileNotFoundError:
    KOS_DATA = []
    logger.warning("⚠️ File kost_data.json tidak ditemukan, memulai dengan data kosong.")
except json.JSONDecodeError:
    KOS_DATA = []

def save_kos_data():
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(KOS_DATA, f, indent=2, ensure_ascii=False)

# --- Data Pengusaha ---
try:
    with open(PENGUSAHA_PATH, "r", encoding="utf-8") as f:
        PENGUSAHA_DATA = json.load(f)
    logger.info(f"✅ Pengusaha terdaftar: {len(PENGUSAHA_DATA)}")
except FileNotFoundError:
    PENGUSAHA_DATA = []
    logger.warning("⚠️ File pengusaha.json tidak ditemukan, memulai dengan daftar kosong.")
except json.JSONDecodeError:
    PENGUSAHA_DATA = []

def save_pengusaha_data():
    with open(PENGUSAHA_PATH, "w", encoding="utf-8") as f:
        json.dump(PENGUSAHA_DATA, f, indent=2, ensure_ascii=False)

# ==================== SESSION & TEMP ====================
user_sessions = {}
upload_temp = {}

# ==================== FUNGSI PEMBANTU ====================
def escape_markdown(text):
    if not isinstance(text, str):
        return text
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

def get_image_path(filename):
    if not isinstance(filename, str):
        filename = ""
    return os.path.join(IMAGE_DIR, filename)

def get_pemilik(user_id):
    for p in PENGUSAHA_DATA:
        if p.get("user_id") == user_id:
            return p
    return None

def is_admin(user_id):
    return user_id == ADMIN_ID

def build_caption(kos, index, total):
    status = kos.get('status', 'approved')
    status_emoji = "✅" if status == "approved" else "⏳" if status == "pending" else "❌"
    kamar = kos.get('kamar_kosong', '?')
    jenis = kos.get('jenis_kos', 'Tidak disebutkan')
    gambar_lain = kos.get('gambar_lain', '')
    link_maps = kos.get('link_maps', '')
    caption = (
        f"📌 *{escape_markdown(kos['nama'])}*\n\n"
        f"{escape_markdown(kos['deskripsi'])}\n\n"
        f"💰 Harga: Rp {kos['harga']:,} / bulan\n"
        f"📍 Lokasi: {escape_markdown(kos['lokasi'])}\n"
        f"👥 Jenis: {escape_markdown(jenis)}\n"
        f"🛏️ Kamar kosong: {kamar}\n"
        f"📊 Status: {status_emoji} {status.upper()}\n"
    )
    if link_maps and link_maps != "":
        caption += f"\n📍 *Google Maps:* [Lihat di peta]({link_maps})"
    if gambar_lain and gambar_lain != "Tidak ada" and gambar_lain != "":
        caption += f"\n📎 *Foto Tambahan:* [Lihat di sini]({gambar_lain})"
    caption += f"\n\n🏠 Kos {index+1} dari {total}"
    return caption

def build_nav_keyboard(index, total, kos=None):
    buttons = []
    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton("◀️ Sebelumnya", callback_data="nav_prev"))
    nav_row.append(InlineKeyboardButton("❌ Tutup", callback_data="nav_close"))
    if index < total - 1:
        nav_row.append(InlineKeyboardButton("▶️ Berikutnya", callback_data="nav_next"))
    buttons.append(nav_row)

    if kos and kos.get('status') == 'approved' and kos.get('pemilik'):
        pemilik = get_pemilik(kos['pemilik'])
        if pemilik and pemilik.get('hp'):
            wa_link = f"https://wa.me/{pemilik['hp']}"
            buttons.append([InlineKeyboardButton("📞 Hubungi Pemilik", url=wa_link)])  # perbaikan syntax

    buttons.append([InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="main")])
    return InlineKeyboardMarkup(buttons)

async def delete_message_safe(context, chat_id, message_id):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"Gagal hapus pesan {message_id}: {e}")

async def safe_answer(query, text=None, show_alert=False):
    try:
        await query.answer(text, show_alert=show_alert)
    except Exception as e:
        logger.warning(f"Gagal menjawab callback: {e}")

async def safe_edit_caption(context, query, caption, reply_markup=None, parse_mode="Markdown"):
    try:
        await query.edit_message_caption(
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except Exception as e:
        logger.warning(f"Gagal edit caption, kirim pesan baru: {e}")
        if query.message:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=caption,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            await delete_message_safe(context, query.message.chat_id, query.message.message_id)

# ==================== MENU UTAMA ====================
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📍 Cari berdasarkan Lokasi", callback_data="menu_location")],
        [InlineKeyboardButton("👨‍💼 Pengusaha Kos", callback_data="menu_pengusaha")],
    ]
    if is_admin(update.effective_user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    reply = InlineKeyboardMarkup(keyboard)
    text = "🏠 **Cari Kos Malang**\nSilakan pilih menu:"

    if update.message:
        welcome_path = get_image_path("wellcome.jpg")
        if os.path.exists(welcome_path):
            with open(welcome_path, "rb") as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption="👋 *Selamat datang di KOASSIAN BOT!*\n\nSaya akan membantu Anda mencari kos di Malang dengan mudah.\nSilakan pilih menu di bawah ini:\n\n" + text,
                    reply_markup=reply,
                    parse_mode="Markdown"
                )
        else:
            welcome = (
                "👋 *Selamat datang di KOASSIAN BOT!*\n\n"
                "Saya akan membantu Anda mencari kos di Malang dengan mudah.\n"
                "Silakan pilih menu di bawah ini:\n\n"
            )
            await update.message.reply_text(welcome + text, reply_markup=reply, parse_mode="Markdown")
    else:
        query = update.callback_query
        if query.message:
            await delete_message_safe(context, query.message.chat_id, query.message.message_id)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=reply,
            parse_mode="Markdown"
        )

# ==================== MENU LOKASI ====================
async def show_location_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lokasi_map = {}
    for k in KOS_DATA:
        if k.get("status") == "approved":
            lower = k["lokasi"].lower()
            if lower not in lokasi_map:
                lokasi_map[lower] = k["lokasi"]
    if not lokasi_map:
        keyboard = [[InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="main")]]
        reply = InlineKeyboardMarkup(keyboard)
        if query.message:
            await delete_message_safe(context, query.message.chat_id, query.message.message_id)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Belum ada kos yang disetujui.",
            reply_markup=reply
        )
        return
    lokasi_set = sorted(lokasi_map.keys())
    keyboard = []
    for lower in lokasi_set:
        original = lokasi_map[lower]
        label = original.title()
        keyboard.append([InlineKeyboardButton(label, callback_data=f"location_{original}")])
    keyboard.append([InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="main")])
    reply = InlineKeyboardMarkup(keyboard)
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📍 **Pilih Lokasi Kos**",
        reply_markup=reply,
        parse_mode="Markdown"
    )

# ==================== PENCARIAN ====================
async def search_by_location(update, context, lokasi):
    query = update.callback_query
    results = [k for k in KOS_DATA if k["lokasi"].lower() == lokasi.lower() and k.get("status") == "approved"]
    if not results:
        keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data="menu_location")]]
        reply = InlineKeyboardMarkup(keyboard)
        if query.message:
            await delete_message_safe(context, query.message.chat_id, query.message.message_id)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Tidak ada kos di lokasi {lokasi}.",
            reply_markup=reply
        )
        return
    await show_result(update, context, results, 0)

async def show_result(update, context, kos_list, index):
    query = update.callback_query
    user_id = update.effective_user.id
    kos = kos_list[index]
    caption = build_caption(kos, index, len(kos_list))
    reply = build_nav_keyboard(index, len(kos_list), kos)

    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)

    image_path = get_image_path(kos.get("gambar", ""))
    sent_message = None
    if os.path.exists(image_path):
        try:
            with open(image_path, "rb") as photo:
                sent_message = await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=photo,
                    caption=caption,
                    reply_markup=reply,
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.warning(f"Gagal kirim gambar: {e}")

    if not sent_message:
        sent_message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"{caption}\n\n⚠️ *Gambar tidak tersedia*",
            reply_markup=reply,
            parse_mode="Markdown"
        )

    user_sessions[user_id] = {
        "list": kos_list,
        "index": index,
        "message_id": sent_message.message_id
    }

async def navigate_slide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id

    session = user_sessions.get(user_id)
    if not session:
        await safe_answer(query, "Sesi pencarian habis. /start kembali.", show_alert=True)
        return

    if data == "nav_close":
        await delete_message_safe(context, update.effective_chat.id, session["message_id"])
        user_sessions.pop(user_id, None)
        await safe_answer(query, "Pencarian ditutup.")
        # Tampilkan menu lokasi
        keyboard = []
        lokasi_map = {}
        for k in KOS_DATA:
            if k.get("status") == "approved":
                lower = k["lokasi"].lower()
                if lower not in lokasi_map:
                    lokasi_map[lower] = k["lokasi"]
        if lokasi_map:
            lokasi_set = sorted(lokasi_map.keys())
            for lower in lokasi_set:
                original = lokasi_map[lower]
                label = original.title()
                keyboard.append([InlineKeyboardButton(label, callback_data=f"location_{original}")])
            keyboard.append([InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="main")])
            reply = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="📍 **Pilih Lokasi Kos**",
                reply_markup=reply,
                parse_mode="Markdown"
            )
        else:
            keyboard = [[InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="main")]]
            reply = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Belum ada kos yang disetujui.",
                reply_markup=reply
            )
        return

    if data == "nav_prev":
        new_index = session["index"] - 1
    elif data == "nav_next":
        new_index = session["index"] + 1
    else:
        await safe_answer(query, "Aksi tidak dikenal.", show_alert=True)
        return

    if new_index < 0 or new_index >= len(session["list"]):
        await safe_answer(query, "Sudah di batas pencarian.", show_alert=True)
        return

    session["index"] = new_index
    kos = session["list"][new_index]
    caption = build_caption(kos, new_index, len(session["list"]))
    reply = build_nav_keyboard(new_index, len(session["list"]), kos)

    await delete_message_safe(context, update.effective_chat.id, session["message_id"])

    image_path = get_image_path(kos.get("gambar", ""))
    sent_message = None
    if os.path.exists(image_path):
        try:
            with open(image_path, "rb") as photo:
                sent_message = await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=photo,
                    caption=caption,
                    reply_markup=reply,
                    parse_mode="Markdown"
                )
        except:
            pass
    if not sent_message:
        sent_message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"{caption}\n\n⚠️ *Gambar tidak tersedia*",
            reply_markup=reply,
            parse_mode="Markdown"
        )
    session["message_id"] = sent_message.message_id
    await safe_answer(query)

# ==================== MENU PENGUSAHA ====================
REGISTER_NAME, REGISTER_HP, EDIT_HP = range(10, 13)

async def show_pengusaha_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id

    pemilik = get_pemilik(user_id)
    if not pemilik or not pemilik.get('nama') or not pemilik.get('hp'):
        await safe_answer(query, "Silakan registrasi terlebih dahulu.")
        await register_start(update, context)
        return

    keyboard = [
        [InlineKeyboardButton("📤 Upload Kos Baru", callback_data="upload_kos")],
        [InlineKeyboardButton("📋 Kos Saya", callback_data="my_kos")],
        [InlineKeyboardButton("📱 Edit Nomor WhatsApp", callback_data="edit_hp")],
        [InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="main")],
    ]
    reply = InlineKeyboardMarkup(keyboard)
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="👨‍💼 **Menu Pengusaha Kos**\nSilakan pilih aksi:",
        reply_markup=reply,
        parse_mode="Markdown"
    )

# ===== Registrasi =====
async def register_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await safe_answer(query)
        if query.message:
            await delete_message_safe(context, query.message.chat_id, query.message.message_id)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="👤 **Registrasi Pengusaha**\nMasukkan *nama lengkap* Anda:\nAtau tekan tombol Batal untuk membatalkan.",
            reply_markup=cancel_button("cancel_register"),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "👤 **Registrasi Pengusaha**\nMasukkan *nama lengkap* Anda:\nAtau tekan /cancel untuk batal.",
            parse_mode="Markdown"
        )
    return REGISTER_NAME

async def register_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['reg_name'] = update.message.text
    await update.message.reply_text(
        "📱 Masukkan *nomor WhatsApp* (contoh: 6281234567890):\nAtau tekan /cancel untuk batal.",
        parse_mode="Markdown"
    )
    return REGISTER_HP

async def register_hp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    nama = context.user_data.get('reg_name')
    hp = update.message.text.strip()
    if not hp.isdigit():
        await update.message.reply_text(
            "❌ Nomor HP harus angka. Coba lagi (contoh: 6281234567890):\nAtau tekan /cancel untuk batal.",
            parse_mode="Markdown"
        )
        return REGISTER_HP

    pemilik = get_pemilik(user_id)
    if pemilik:
        pemilik['nama'] = nama
        pemilik['hp'] = hp
    else:
        PENGUSAHA_DATA.append({"user_id": user_id, "nama": nama, "hp": hp})
    save_pengusaha_data()
    await update.message.reply_text("✅ Registrasi berhasil! Sekarang Anda bisa menggunakan menu pengusaha.")
    keyboard = [
        [InlineKeyboardButton("📤 Upload Kos Baru", callback_data="upload_kos")],
        [InlineKeyboardButton("📋 Kos Saya", callback_data="my_kos")],
        [InlineKeyboardButton("📱 Edit Nomor WhatsApp", callback_data="edit_hp")],
        [InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="main")],
    ]
    reply = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👨‍💼 **Menu Pengusaha Kos**\nSilakan pilih aksi:",
        reply_markup=reply,
        parse_mode="Markdown"
    )
    context.user_data.pop('reg_name', None)
    return ConversationHandler.END

# ===== Edit HP =====
async def edit_hp_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    user_id = update.effective_user.id
    pemilik = get_pemilik(user_id)
    if not pemilik:
        await query.message.delete()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Data pengusaha tidak ditemukan. Silakan registrasi ulang."
        )
        return ConversationHandler.END
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📱 Nomor WhatsApp saat ini: *{pemilik['hp']}*\n\n"
             "Kirim *nomor baru* (contoh: 6281234567890) untuk mengganti.\n"
             "Atau tekan tombol Batal untuk membatalkan.",
        reply_markup=cancel_button("cancel_edit_hp"),
        parse_mode="Markdown"
    )
    return EDIT_HP

async def receive_edit_hp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    hp = update.message.text.strip()
    if not hp.isdigit():
        await update.message.reply_text(
            "❌ Nomor HP harus angka. Coba lagi (contoh: 6281234567890):\nAtau tekan /cancel untuk batal.",
            parse_mode="Markdown"
        )
        return EDIT_HP

    pemilik = get_pemilik(user_id)
    if not pemilik:
        await update.message.reply_text("❌ Data pengusaha tidak ditemukan.")
        return ConversationHandler.END

    pemilik['hp'] = hp
    save_pengusaha_data()
    await update.message.reply_text("✅ Nomor WhatsApp berhasil diperbarui!")
    await show_pengusaha_menu(update, context)
    return ConversationHandler.END

async def cancel_edit_hp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await show_pengusaha_menu(update, context)
    return ConversationHandler.END

# ==================== UPLOAD KOS ====================
NAME, PRICE, LOCATION, JENIS, DESCRIPTION, PHOTO, GAMBAR_LAIN, LINK_MAPS, KAMAR, CONFIRM = range(20, 30)

def cancel_button(callback_data="cancel_upload"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal", callback_data=callback_data)]])

def skip_or_cancel_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ Skip", callback_data="skip_link")],
        [InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]
    ])

async def send_jenis_choice(chat_id, context):
    keyboard = [
        [InlineKeyboardButton("👦 Putra", callback_data="jenis_putra"),
         InlineKeyboardButton("👧 Putri", callback_data="jenis_putri")],
        [InlineKeyboardButton("👫 Campur", callback_data="jenis_campur")],
        [InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]
    ]
    reply = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=chat_id,
        text="👥 Pilih *jenis kos*:",
        reply_markup=reply,
        parse_mode="Markdown"
    )

async def upload_kos_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    user_id = update.effective_user.id
    upload_temp[user_id] = {}
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📤 **Upload Kos Baru**\n\nSilakan kirim *nama kos* (contoh: Kos Melati 2):\n"
             "Atau tekan tombol Batal untuk membatalkan.",
        reply_markup=cancel_button("cancel_upload"),
        parse_mode="Markdown"
    )
    return NAME

async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in upload_temp:
        await update.message.reply_text("❌ Sesi upload tidak valid. Silakan mulai upload baru dengan menekan tombol 'Upload Kos Baru'.")
        return ConversationHandler.END
    upload_temp[user_id]["nama"] = update.message.text
    await update.message.reply_text(
        "💰 Masukkan *harga sewa per bulan* (angka saja):\nAtau tekan tombol Batal untuk membatalkan.",
        reply_markup=cancel_button("cancel_upload"),
        parse_mode="Markdown"
    )
    return PRICE

async def receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in upload_temp:
        await update.message.reply_text("❌ Sesi upload tidak valid. Silakan mulai upload baru.")
        return ConversationHandler.END
    try:
        harga = int(update.message.text.replace(".", "").replace(",", ""))
        if harga <= 0:
            raise ValueError
    except:
        await update.message.reply_text(
            "❌ Harga harus angka positif. Coba lagi:\nAtau tekan tombol Batal untuk membatalkan.",
            reply_markup=cancel_button("cancel_upload"),
            parse_mode="Markdown"
        )
        return PRICE
    upload_temp[user_id]["harga"] = harga
    await show_location_choice(update, context)
    return LOCATION

async def show_location_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lokasi_map = {}
    for k in KOS_DATA:
        if k.get("status") == "approved":
            lower = k["lokasi"].lower()
            if lower not in lokasi_map:
                lokasi_map[lower] = k["lokasi"]
    lokasi_set = sorted(lokasi_map.keys())
    buttons = []
    for lower in lokasi_set:
        original = lokasi_map[lower]
        label = original.title()
        buttons.append([InlineKeyboardButton(label, callback_data=f"loc_{original}")])
    buttons.append([InlineKeyboardButton("➕ Lainnya (ketik manual)", callback_data="loc_manual")])
    buttons.append([InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")])
    reply = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(
        "📍 Pilih *lokasi* kos dari daftar di bawah, atau pilih 'Lainnya' untuk mengetik sendiri:",
        reply_markup=reply,
        parse_mode="Markdown"
    )

async def location_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    await safe_answer(query)

    if user_id not in upload_temp:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Sesi upload tidak valid. Silakan mulai upload baru."
        )
        return ConversationHandler.END

    if data == "loc_manual":
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📍 Masukkan *lokasi* secara manual (contoh: Lowokwaru):\nAtau tekan tombol Batal untuk membatalkan.",
            reply_markup=cancel_button("cancel_upload"),
            parse_mode="Markdown"
        )
        return LOCATION
    else:
        lokasi = data.split("_", 1)[1]
        upload_temp[user_id]["lokasi"] = lokasi
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
        await send_jenis_choice(update.effective_chat.id, context)
        return JENIS

async def receive_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in upload_temp:
        await update.message.reply_text("❌ Sesi upload tidak valid. Silakan mulai upload baru.")
        return ConversationHandler.END
    upload_temp[user_id]["lokasi"] = update.message.text
    await send_jenis_choice(update.effective_chat.id, context)
    return JENIS

async def jenis_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    await safe_answer(query)

    if user_id not in upload_temp:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Sesi upload tidak valid. Silakan mulai upload baru."
        )
        return ConversationHandler.END

    if data.startswith("jenis_"):
        jenis = data.split("_")[1]
        upload_temp[user_id]["jenis_kos"] = jenis
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📝 Masukkan *deskripsi* kos (contoh: Kos nyaman dekat kampus):\nAtau tekan tombol Batal untuk membatalkan.",
            reply_markup=cancel_button("cancel_upload"),
            parse_mode="Markdown"
        )
        return DESCRIPTION
    else:
        return JENIS

async def receive_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in upload_temp:
        await update.message.reply_text("❌ Sesi upload tidak valid. Silakan mulai upload baru.")
        return ConversationHandler.END
    upload_temp[user_id]["deskripsi"] = update.message.text
    await update.message.reply_text(
        "🖼️ Kirimkan *foto utama* kos (satu gambar).\nAtau tekan tombol Batal untuk membatalkan.",
        reply_markup=cancel_button("cancel_upload"),
        parse_mode="Markdown"
    )
    return PHOTO

async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in upload_temp:
        await update.message.reply_text("❌ Sesi upload tidak valid. Silakan mulai upload baru.")
        return ConversationHandler.END
    photo_file = update.message.photo[-1]
    file = await context.bot.get_file(photo_file.file_id)
    timestamp = int(time.time())
    filename = f"upload_{user_id}_{timestamp}.jpg"
    filepath = get_image_path(filename)
    await file.download_to_drive(filepath)
    upload_temp[user_id]["gambar"] = filename

    await update.message.reply_text(
        "📎 *Gambar tambahan* (opsional):\n"
        "Silakan kirimkan *link Google Drive* yang berisi foto-foto tambahan kos dengan akses siapa saja yang memiliki link.\n"
        "Atau tekan tombol 'Skip' untuk melewati.",
        reply_markup=skip_or_cancel_button(),
        parse_mode="Markdown"
    )
    return GAMBAR_LAIN

# ===== SKIP LINK CALLBACK UNTUK GAMBAR_LAIN =====
async def skip_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    user_id = update.effective_user.id
    if user_id not in upload_temp:
        await query.message.delete()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Sesi upload tidak valid. Silakan mulai upload baru."
        )
        return ConversationHandler.END
    upload_temp[user_id]["gambar_lain"] = ""
    await query.message.delete()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✅ Gambar tambahan dilewati."
    )
    # Lanjut ke LINK_MAPS
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📍 *Link Google Maps* (opsional):\nSilakan kirimkan link Google Maps lokasi kos.\nAtau tekan tombol 'Skip' untuk melewati.",
        reply_markup=skip_or_cancel_button(),
        parse_mode="Markdown"
    )
    return LINK_MAPS

async def receive_gambar_lain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in upload_temp:
        await update.message.reply_text("❌ Sesi upload tidak valid. Silakan mulai upload baru.")
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.lower() == 'skip':
        upload_temp[user_id]["gambar_lain"] = ""
        await update.message.reply_text("✅ Gambar tambahan dilewati.")
    else:
        if not (text.startswith('http://') or text.startswith('https://')):
            await update.message.reply_text(
                "❌ Link tidak valid. Harap masukkan link yang benar (contoh: https://drive.google.com/...)\n"
                "Atau tekan tombol 'Skip' untuk melewati.",
                reply_markup=skip_or_cancel_button(),
                parse_mode="Markdown"
            )
            return GAMBAR_LAIN
        upload_temp[user_id]["gambar_lain"] = text
        await update.message.reply_text("✅ Link gambar tambahan disimpan.")
    # Lanjut ke LINK_MAPS
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📍 *Link Google Maps* (opsional):\nSilakan kirimkan link Google Maps lokasi kos.\nAtau tekan tombol 'Skip' untuk melewati.",
        reply_markup=skip_or_cancel_button(),
        parse_mode="Markdown"
    )
    return LINK_MAPS

# ===== SKIP LINK MAPS =====
async def skip_link_maps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    user_id = update.effective_user.id
    if user_id not in upload_temp:
        await query.message.delete()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Sesi upload tidak valid. Silakan mulai upload baru."
        )
        return ConversationHandler.END
    upload_temp[user_id]["link_maps"] = ""
    await query.message.delete()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✅ Link Maps dilewati."
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🛏️ Masukkan *jumlah kamar kosong* saat ini (angka):\nAtau tekan tombol Batal untuk membatalkan.",
        reply_markup=cancel_button("cancel_upload"),
        parse_mode="Markdown"
    )
    return KAMAR

async def receive_link_maps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in upload_temp:
        await update.message.reply_text("❌ Sesi upload tidak valid. Silakan mulai upload baru.")
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.lower() == 'skip':
        upload_temp[user_id]["link_maps"] = ""
        await update.message.reply_text("✅ Link Maps dilewati.")
    else:
        if not (text.startswith('http://') or text.startswith('https://')):
            await update.message.reply_text(
                "❌ Link tidak valid. Harap masukkan link yang benar (contoh: https://maps.google.com/...)\n"
                "Atau tekan tombol 'Skip' untuk melewati.",
                reply_markup=skip_or_cancel_button(),
                parse_mode="Markdown"
            )
            return LINK_MAPS
        upload_temp[user_id]["link_maps"] = text
        await update.message.reply_text("✅ Link Maps disimpan.")
    await update.message.reply_text(
        "🛏️ Masukkan *jumlah kamar kosong* saat ini (angka):\nAtau tekan tombol Batal untuk membatalkan.",
        reply_markup=cancel_button("cancel_upload"),
        parse_mode="Markdown"
    )
    return KAMAR

async def receive_kamar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in upload_temp:
        await update.message.reply_text("❌ Sesi upload tidak valid. Silakan mulai upload baru.")
        return ConversationHandler.END
    try:
        kamar = int(update.message.text)
        if kamar < 0:
            raise ValueError
    except:
        await update.message.reply_text(
            "❌ Jumlah kamar harus angka non-negatif. Coba lagi:\nAtau tekan tombol Batal untuk membatalkan.",
            reply_markup=cancel_button("cancel_upload"),
            parse_mode="Markdown"
        )
        return KAMAR
    upload_temp[user_id]["kamar_kosong"] = kamar

    data = upload_temp[user_id]
    summary = (
        f"📋 *Konfirmasi Data Kos Baru*\n\n"
        f"📌 Nama: {escape_markdown(data['nama'])}\n"
        f"💰 Harga: Rp {data['harga']:,}\n"
        f"📍 Lokasi: {escape_markdown(data['lokasi'])}\n"
        f"👥 Jenis: {escape_markdown(data.get('jenis_kos', ''))}\n"
        f"📝 Deskripsi: {escape_markdown(data['deskripsi'])}\n"
        f"🛏️ Kamar kosong: {data['kamar_kosong']}\n"
        f"🖼️ Gambar: {data['gambar']}\n"
        f"📎 Gambar lain: {escape_markdown(data.get('gambar_lain', 'Tidak ada'))}\n"
        f"📍 Maps: {escape_markdown(data.get('link_maps', 'Tidak ada'))}\n\n"
        f"⚠️ *Kos akan masuk antrian verifikasi admin.*\nApakah data sudah benar?"
    )
    keyboard = [
        [InlineKeyboardButton("✅ Simpan", callback_data="confirm_yes"),
         InlineKeyboardButton("❌ Batal", callback_data="confirm_no")]
    ]
    reply = InlineKeyboardMarkup(keyboard)
    filepath = get_image_path(data["gambar"])
    try:
        with open(filepath, "rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=summary,
                reply_markup=reply,
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.warning(f"Gagal kirim gambar konfirmasi: {e}")
        await update.message.reply_text(
            text=summary + "\n\n⚠️ Gambar tidak dapat ditampilkan, tetapi tetap disimpan.",
            reply_markup=reply,
            parse_mode="Markdown"
        )
    return CONFIRM

async def confirm_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    user_id = update.effective_user.id
    data = upload_temp.get(user_id)

    if not data:
        await safe_edit_caption(context, query, "⚠️ Data tidak ditemukan, ulangi upload.")
        return ConversationHandler.END

    if query.data == "confirm_yes":
        new_id = max([k["id"] for k in KOS_DATA], default=0) + 1
        kos_baru = {
            "id": new_id,
            "nama": data["nama"],
            "harga": data["harga"],
            "lokasi": data["lokasi"],
            "jenis_kos": data.get("jenis_kos", ""),
            "deskripsi": data["deskripsi"],
            "gambar": data["gambar"],
            "gambar_lain": data.get("gambar_lain", ""),
            "link_maps": data.get("link_maps", ""),
            "kamar_kosong": data.get("kamar_kosong", 0),
            "pemilik": user_id,
            "status": "pending"
        }
        KOS_DATA.append(kos_baru)
        save_kos_data()
        await safe_edit_caption(
            context, query,
            "✅ **Kos berhasil diupload dan menunggu verifikasi admin.**\n"
            "Status akan berubah menjadi 'approved' setelah disetujui.",
            parse_mode="Markdown"
        )
        logger.info(f"Kos baru oleh user {user_id}: {kos_baru['nama']} (pending)")

        if ADMIN_ID != 0:
            try:
                pemilik = get_pemilik(user_id)
                nama_pemilik = pemilik['nama'] if pemilik else str(user_id)
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🆕 *Kos Baru Pending Verifikasi!*\n\n"
                         f"📌 Nama: {kos_baru['nama']}\n"
                         f"💰 Harga: Rp {kos_baru['harga']:,}\n"
                         f"📍 Lokasi: {kos_baru['lokasi']}\n"
                         f"👤 Pemilik: {nama_pemilik} (ID: {user_id})\n"
                         f"🆔 ID Kos: {new_id}\n\n"
                         f"Gunakan /start lalu pilih Admin Panel untuk verifikasi.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Gagal kirim notifikasi ke admin: {e}")
    else:
        if os.path.exists(get_image_path(data.get("gambar", ""))):
            os.remove(get_image_path(data["gambar"]))
        await safe_edit_caption(context, query, "❌ Upload dibatalkan.")

    upload_temp.pop(user_id, None)
    await show_pengusaha_menu(update, context)
    return ConversationHandler.END

async def cancel_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = upload_temp.pop(user_id, None)
    if data and os.path.exists(get_image_path(data.get("gambar", ""))):
        os.remove(get_image_path(data["gambar"]))
    await update.message.reply_text("❌ Upload dibatalkan.")
    await show_pengusaha_menu(update, context)
    return ConversationHandler.END

async def cancel_upload_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    user_id = update.effective_user.id
    data = upload_temp.pop(user_id, None)
    if data and os.path.exists(get_image_path(data.get("gambar", ""))):
        os.remove(get_image_path(data["gambar"]))
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await show_pengusaha_menu(update, context)
    return ConversationHandler.END

# ==================== KOS SAYA ====================
async def show_my_kos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    my_kos = [k for k in KOS_DATA if k.get("pemilik") == user_id]

    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    if not my_kos:
        keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data="menu_pengusaha")]]
        reply = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📋 Anda belum memiliki kos.",
            reply_markup=reply
        )
        return

    keyboard = []
    for kos in my_kos:
        status_emoji = "✅" if kos.get("status") == "approved" else "⏳" if kos.get("status") == "pending" else "❌"
        label = f"{status_emoji} {escape_markdown(kos['nama'])} - Rp {kos['harga']:,}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"detail_{kos['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="menu_pengusaha")])
    reply = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📋 **Daftar Kos Anda**",
        reply_markup=reply,
        parse_mode="Markdown"
    )

async def show_kos_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    kos_id = int(query.data.split("_")[1])
    kos = next((k for k in KOS_DATA if k["id"] == kos_id), None)

    if not kos:
        await safe_answer(query, "Kos tidak ditemukan", show_alert=True)
        return

    caption = (
        f"📌 *{escape_markdown(kos['nama'])}*\n"
        f"💰 Rp {kos['harga']:,}\n"
        f"📍 {escape_markdown(kos['lokasi'])}\n"
        f"👥 {escape_markdown(kos.get('jenis_kos', 'Tidak disebutkan'))}\n"
        f"🛏️ Kamar kosong: {kos.get('kamar_kosong', 0)}\n"
        f"📝 {escape_markdown(kos['deskripsi'])}\n"
        f"📊 Status: {kos.get('status', 'approved').upper()}\n"
    )
    if kos.get('link_maps'):
        caption += f"📍 *Google Maps:* [Lihat di peta]({kos['link_maps']})\n"
    if kos.get('gambar_lain'):
        caption += f"📎 *Foto Tambahan:* [Lihat di sini]({kos['gambar_lain']})\n"

    keyboard = [
        [InlineKeyboardButton("🔄 Update Kamar", callback_data=f"update_{kos_id}")],
        [InlineKeyboardButton("✏️ Edit Deskripsi", callback_data=f"edit_desc_{kos_id}")],
        [InlineKeyboardButton("🖼️ Ganti Foto Utama", callback_data=f"edit_photo_{kos_id}")],
        [InlineKeyboardButton("📎 Edit Link Foto", callback_data=f"edit_link_{kos_id}")],
        [InlineKeyboardButton("🗑️ Hapus Kos", callback_data=f"delete_{kos_id}")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="my_kos")]
    ]
    reply = InlineKeyboardMarkup(keyboard)

    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    image_path = get_image_path(kos.get("gambar", ""))
    if os.path.exists(image_path):
        with open(image_path, "rb") as photo:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo,
                caption=caption,
                reply_markup=reply,
                parse_mode="Markdown"
            )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"{caption}\n\n⚠️ Gambar tidak tersedia",
            reply_markup=reply,
            parse_mode="Markdown"
        )
    await safe_answer(query)

# ==================== EDIT DESKRIPSI ====================
EDIT_DESC = 40

async def edit_desc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    kos_id = int(query.data.split("_")[2])
    context.user_data['edit_kos_id'] = kos_id
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✏️ Kirim *deskripsi baru* untuk kos ini:\nAtau tekan tombol Batal untuk membatalkan.",
        reply_markup=cancel_button("cancel_edit_desc"),
        parse_mode="Markdown"
    )
    return EDIT_DESC

async def receive_edit_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    kos_id = context.user_data.get('edit_kos_id')
    kos = next((k for k in KOS_DATA if k['id'] == kos_id and k.get('pemilik') == user_id), None)
    if not kos:
        await update.message.reply_text("❌ Kos tidak ditemukan atau Anda tidak berhak.")
        return ConversationHandler.END

    kos['deskripsi'] = update.message.text
    save_kos_data()
    await update.message.reply_text("✅ Deskripsi kos berhasil diperbarui!")
    context.user_data.pop('edit_kos_id', None)
    await show_kos_detail(update, context)
    return ConversationHandler.END

async def cancel_edit_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    context.user_data.pop('edit_kos_id', None)
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await show_my_kos(update, context)
    return ConversationHandler.END

# ==================== EDIT FOTO UTAMA ====================
EDIT_PHOTO = 42

async def edit_photo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    kos_id = int(query.data.split("_")[2])
    context.user_data['edit_photo_kos_id'] = kos_id
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🖼️ Kirim *foto baru* untuk kos ini (satu gambar).\nAtau tekan tombol Batal untuk membatalkan.",
        reply_markup=cancel_button("cancel_edit_photo"),
        parse_mode="Markdown"
    )
    return EDIT_PHOTO

async def receive_edit_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    kos_id = context.user_data.get('edit_photo_kos_id')
    kos = next((k for k in KOS_DATA if k['id'] == kos_id and k.get('pemilik') == user_id), None)
    if not kos:
        await update.message.reply_text("❌ Kos tidak ditemukan atau Anda tidak berhak.")
        return ConversationHandler.END

    photo_file = update.message.photo[-1]
    file = await context.bot.get_file(photo_file.file_id)
    timestamp = int(time.time())
    filename = f"upload_{user_id}_{timestamp}.jpg"
    filepath = get_image_path(filename)
    await file.download_to_drive(filepath)

    # Hapus foto lama
    old_image = kos.get("gambar")
    if old_image and os.path.exists(get_image_path(old_image)):
        os.remove(get_image_path(old_image))

    kos['gambar'] = filename
    save_kos_data()
    await update.message.reply_text("✅ Foto utama berhasil diperbarui!")
    context.user_data.pop('edit_photo_kos_id', None)
    await show_kos_detail(update, context)
    return ConversationHandler.END

async def cancel_edit_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    context.user_data.pop('edit_photo_kos_id', None)
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await show_my_kos(update, context)
    return ConversationHandler.END

# ==================== EDIT LINK FOTO TAMBAHAN ====================
EDIT_LINK = 41

async def edit_link_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    kos_id = int(query.data.split("_")[2])
    context.user_data['edit_link_kos_id'] = kos_id
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📎 Kirim *link baru* untuk foto tambahan (Google Drive).\n"
             "Pastikan link memiliki akses 'Siapa saja yang memiliki link'.\n"
             "Atau ketik *hapus* untuk menghapus link yang ada.\n"
             "Tekan tombol Batal untuk membatalkan.",
        reply_markup=cancel_button("cancel_edit_link"),
        parse_mode="Markdown"
    )
    return EDIT_LINK

async def receive_edit_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    kos_id = context.user_data.get('edit_link_kos_id')
    kos = next((k for k in KOS_DATA if k['id'] == kos_id and k.get('pemilik') == user_id), None)
    if not kos:
        await update.message.reply_text("❌ Kos tidak ditemukan atau Anda tidak berhak.")
        return ConversationHandler.END

    text = update.message.text.strip()
    if text.lower() == 'hapus':
        kos['gambar_lain'] = ""
        await update.message.reply_text("✅ Link foto tambahan telah dihapus.")
    else:
        if not (text.startswith('http://') or text.startswith('https://')):
            await update.message.reply_text(
                "❌ Link tidak valid. Harap masukkan link yang benar (contoh: https://drive.google.com/...)\n"
                "Atau ketik *hapus* untuk menghapus link.",
                parse_mode="Markdown"
            )
            return EDIT_LINK
        kos['gambar_lain'] = text
        await update.message.reply_text("✅ Link foto tambahan berhasil diperbarui!")

    save_kos_data()
    context.user_data.pop('edit_link_kos_id', None)
    await show_kos_detail(update, context)
    return ConversationHandler.END

async def cancel_edit_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    context.user_data.pop('edit_link_kos_id', None)
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await show_my_kos(update, context)
    return ConversationHandler.END

# ==================== UPDATE KAMAR KOSONG ====================
UPDATE_KAMAR, UPDATE_DESC = range(30, 32)

async def update_kos_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    kos_id = int(query.data.split("_")[1])
    context.user_data['update_kos_id'] = kos_id
    await safe_answer(query)
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✏️ Masukkan *jumlah kamar kosong* yang baru (angka):\nAtau tekan tombol Batal untuk membatalkan.",
        reply_markup=cancel_button("cancel_update"),
        parse_mode="Markdown"
    )
    return UPDATE_KAMAR

async def update_kamar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        kamar = int(update.message.text)
        if kamar < 0:
            raise ValueError
    except:
        await update.message.reply_text(
            "❌ Jumlah harus angka non-negatif. Coba lagi:\nAtau tekan tombol Batal untuk membatalkan.",
            reply_markup=cancel_button("cancel_update"),
            parse_mode="Markdown"
        )
        return UPDATE_KAMAR
    context.user_data['update_kamar'] = kamar
    await update.message.reply_text(
        "📝 Kirim *deskripsi baru* (atau ketik 'same' untuk tetap sama):\nAtau tekan tombol Batal untuk membatalkan.",
        reply_markup=cancel_button("cancel_update"),
        parse_mode="Markdown"
    )
    return UPDATE_DESC

async def update_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    kos_id = context.user_data.get('update_kos_id')
    kos = next((k for k in KOS_DATA if k['id'] == kos_id and k.get('pemilik') == user_id), None)
    if not kos:
        await update.message.reply_text("❌ Kos tidak ditemukan atau Anda tidak berhak.")
        return ConversationHandler.END

    desc = update.message.text
    if desc.lower() != 'same':
        kos['deskripsi'] = desc
    kos['kamar_kosong'] = context.user_data.get('update_kamar')
    kos['status'] = 'pending'
    save_kos_data()

    await update.message.reply_text(
        "✅ Perubahan disimpan. Kos akan diverifikasi ulang oleh admin.\n"
        "Status saat ini: PENDING"
    )
    context.user_data.pop('update_kos_id', None)
    context.user_data.pop('update_kamar', None)
    await show_kos_detail(update, context)
    return ConversationHandler.END

async def cancel_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    context.user_data.pop('update_kos_id', None)
    context.user_data.pop('update_kamar', None)
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    await show_my_kos(update, context)
    return ConversationHandler.END

# ==================== HAPUS KOS ====================
async def delete_kos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    kos_id = int(query.data.split("_")[1])
    user_id = update.effective_user.id

    kos = next((k for k in KOS_DATA if k["id"] == kos_id and k.get("pemilik") == user_id), None)
    if not kos:
        await safe_answer(query, "Anda tidak berhak menghapus kos ini.", show_alert=True)
        return

    KOS_DATA.remove(kos)
    save_kos_data()
    image_path = get_image_path(kos.get("gambar", ""))
    if os.path.exists(image_path):
        os.remove(image_path)

    await safe_answer(query, "✅ Kos berhasil dihapus.")
    await show_my_kos(update, context)

# ==================== ADMIN PANEL ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await safe_answer(update.callback_query, "Anda bukan admin.", show_alert=True)
        return
    query = update.callback_query
    pending = [k for k in KOS_DATA if k.get("status") == "pending"]
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    if not pending:
        keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data="main")]]
        reply = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✅ Tidak ada kos yang menunggu verifikasi.",
            reply_markup=reply
        )
        return

    keyboard = []
    for kos in pending:
        label = f"⏳ {escape_markdown(kos['nama'])} - Rp {kos['harga']:,} (oleh {kos['pemilik']})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"admin_detail_{kos['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="main")])
    reply = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📋 **Daftar Kos Pending Verifikasi**",
        reply_markup=reply,
        parse_mode="Markdown"
    )

async def admin_detail_kos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await safe_answer(update.callback_query, "Anda bukan admin.", show_alert=True)
        return
    query = update.callback_query
    kos_id = int(query.data.split("_")[2])
    kos = next((k for k in KOS_DATA if k["id"] == kos_id), None)
    if not kos:
        await safe_answer(query, "Kos tidak ditemukan.", show_alert=True)
        return

    pemilik = get_pemilik(kos['pemilik'])
    pemilik_info = f"Nama: {pemilik['nama']}\nHP: {pemilik['hp']}" if pemilik else "Tidak diketahui"

    caption = (
        f"📌 *{escape_markdown(kos['nama'])}*\n"
        f"💰 Rp {kos['harga']:,}\n"
        f"📍 {escape_markdown(kos['lokasi'])}\n"
        f"👥 {escape_markdown(kos.get('jenis_kos', 'Tidak disebutkan'))}\n"
        f"🛏️ Kamar kosong: {kos.get('kamar_kosong', 0)}\n"
        f"📝 {escape_markdown(kos['deskripsi'])}\n"
        f"👤 Pemilik:\n{pemilik_info}\n"
    )
    if kos.get('link_maps'):
        caption += f"📍 *Google Maps:* [Lihat di peta]({kos['link_maps']})\n"
    if kos.get('gambar_lain'):
        caption += f"📎 *Foto Tambahan:* [Lihat di sini]({kos['gambar_lain']})\n"

    keyboard = [
        [InlineKeyboardButton("✅ Setujui", callback_data=f"approve_{kos_id}"),
         InlineKeyboardButton("❌ Tolak", callback_data=f"reject_{kos_id}")],
        [InlineKeyboardButton("🔙 Kembali ke Daftar", callback_data="admin_panel")]
    ]
    reply = InlineKeyboardMarkup(keyboard)
    if query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    image_path = get_image_path(kos.get("gambar", ""))
    if os.path.exists(image_path):
        with open(image_path, "rb") as photo:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo,
                caption=caption,
                reply_markup=reply,
                parse_mode="Markdown"
            )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"{caption}\n\n⚠️ Gambar tidak tersedia",
            reply_markup=reply,
            parse_mode="Markdown"
        )
    await safe_answer(query)

async def approve_kos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await safe_answer(update.callback_query, "Anda bukan admin.", show_alert=True)
        return
    query = update.callback_query
    kos_id = int(query.data.split("_")[1])
    kos = next((k for k in KOS_DATA if k["id"] == kos_id), None)
    if not kos:
        await safe_answer(query, "Kos tidak ditemukan.", show_alert=True)
        return
    kos['status'] = 'approved'
    save_kos_data()
    await safe_answer(query, "✅ Kos disetujui.")
    await admin_panel(update, context)

async def reject_kos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await safe_answer(update.callback_query, "Anda bukan admin.", show_alert=True)
        return
    query = update.callback_query
    kos_id = int(query.data.split("_")[1])
    kos = next((k for k in KOS_DATA if k["id"] == kos_id), None)
    if not kos:
        await safe_answer(query, "Kos tidak ditemukan.", show_alert=True)
        return
    KOS_DATA.remove(kos)
    save_kos_data()
    image_path = get_image_path(kos.get("gambar", ""))
    if os.path.exists(image_path):
        os.remove(image_path)
    await safe_answer(query, "❌ Kos ditolak dan dihapus.")
    await admin_panel(update, context)

# ==================== PENANGANAN PERINTAH TIDAK DIKENAL ====================
async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Perintah yang Anda berikan tidak sesuai.\n"
        "Silakan gunakan tombol yang tersedia atau ketik /start untuk memulai."
    )

# ==================== ROUTER CALLBACK ====================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await safe_answer(query)

    if data == "main":
        await show_main_menu(update, context)
    elif data == "menu_location":
        await show_location_menu(update, context)
    elif data == "menu_pengusaha":
        await show_pengusaha_menu(update, context)
    elif data == "admin_panel":
        await admin_panel(update, context)
    elif data == "upload_kos":
        await upload_kos_start(update, context)
        return
    elif data == "my_kos":
        await show_my_kos(update, context)
    elif data == "edit_hp":
        await edit_hp_start(update, context)
        return
    elif data.startswith("cancel_"):
        if data == "cancel_upload":
            await cancel_upload_callback(update, context)
            return
        elif data == "cancel_edit_hp":
            await cancel_edit_hp(update, context)
            return
        elif data == "cancel_edit_desc":
            await cancel_edit_desc(update, context)
            return
        elif data == "cancel_edit_photo":
            await cancel_edit_photo(update, context)
            return
        elif data == "cancel_edit_link":
            await cancel_edit_link(update, context)
            return
        elif data == "cancel_update":
            await cancel_update(update, context)
            return
        elif data == "cancel_register":
            await cancel_upload_callback(update, context)
            return
    elif data == "skip_link":
        # skip_link digunakan di dua state, biarkan conversation handler yang menangani
        pass
    elif data.startswith("loc_"):
        await location_callback(update, context)
        return
    elif data.startswith("jenis_"):
        await jenis_callback(update, context)
        return
    elif data.startswith("detail_"):
        await show_kos_detail(update, context)
    elif data.startswith("delete_"):
        await delete_kos(update, context)
    elif data.startswith("update_"):
        await update_kos_start(update, context)
        return
    elif data.startswith("edit_desc_"):
        await edit_desc_start(update, context)
        return
    elif data.startswith("edit_photo_"):
        await edit_photo_start(update, context)
        return
    elif data.startswith("edit_link_"):
        await edit_link_start(update, context)
        return
    elif data.startswith("admin_detail_"):
        await admin_detail_kos(update, context)
    elif data.startswith("approve_"):
        await approve_kos(update, context)
    elif data.startswith("reject_"):
        await reject_kos(update, context)
    elif data.startswith("location_"):
        lokasi = data.split("_", 1)[1]
        await search_by_location(update, context, lokasi)
    elif data.startswith("nav_"):
        await navigate_slide(update, context)
    else:
        await show_main_menu(update, context)

# ==================== MAIN ====================
def main():
    app = Application.builder().token(TOKEN).read_timeout(30).connect_timeout(20).build()

    upload_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(upload_kos_start, pattern="^upload_kos$")],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price)],
            LOCATION: [
                CallbackQueryHandler(location_callback, pattern="^loc_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_location)
            ],
            JENIS: [
                CallbackQueryHandler(jenis_callback, pattern="^jenis_"),
            ],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_description)],
            PHOTO: [MessageHandler(filters.PHOTO, receive_photo)],
            GAMBAR_LAIN: [
                CallbackQueryHandler(skip_link_callback, pattern="^skip_link$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_gambar_lain)
            ],
            LINK_MAPS: [
                CallbackQueryHandler(skip_link_maps_callback, pattern="^skip_link$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link_maps)
            ],
            KAMAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_kamar)],
            CONFIRM: [CallbackQueryHandler(confirm_upload, pattern="^confirm_")]
        },
        fallbacks=[CommandHandler("cancel", cancel_upload)],
    )

    update_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(update_kos_start, pattern="^update_")],
        states={
            UPDATE_KAMAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_kamar)],
            UPDATE_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_desc)],
        },
        fallbacks=[CommandHandler("cancel", cancel_update)],
    )

    edit_desc_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_desc_start, pattern="^edit_desc_")],
        states={
            EDIT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_desc)],
        },
        fallbacks=[CommandHandler("cancel", cancel_edit_desc)],
    )

    edit_photo_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_photo_start, pattern="^edit_photo_")],
        states={
            EDIT_PHOTO: [MessageHandler(filters.PHOTO, receive_edit_photo)],
        },
        fallbacks=[CommandHandler("cancel", cancel_edit_photo)],
    )

    edit_link_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_link_start, pattern="^edit_link_")],
        states={
            EDIT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel_edit_link)],
    )

    edit_hp_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_hp_start, pattern="^edit_hp$")],
        states={
            EDIT_HP: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_hp)],
        },
        fallbacks=[CommandHandler("cancel", cancel_edit_hp)],
    )

    register_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(register_start, pattern="^register$")],
        states={
            REGISTER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_name)],
            REGISTER_HP: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_hp)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)],
    )

    app.add_handler(CommandHandler("start", show_main_menu))
    app.add_handler(upload_conv)
    app.add_handler(update_conv)
    app.add_handler(edit_desc_conv)
    app.add_handler(edit_photo_conv)
    app.add_handler(edit_link_conv)
    app.add_handler(edit_hp_conv)
    app.add_handler(register_conv)
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))

    logger.info("🤖 Bot Malang dengan verifikasi admin dan fitur edit berjalan...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()