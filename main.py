import os
import shutil
import hashlib
from datetime import datetime
import json
import sqlite3
import tempfile

# --- Kivy Imports ---
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup
from kivy.uix.spinner import Spinner
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.utils import platform
from kivy.graphics import Color, Rectangle, RoundedRectangle

# --- PDF and Image Imports ---
from fpdf import FPDF
try:
    from PIL import Image as PILImage, ImageDraw, ImageFont
except ImportError:
    PILImage = None

import socket
import threading

# --- Settings & Constants ---
def get_data_dir():
    try:
        if platform == 'android':
            from android.storage import app_storage_path
            path = app_storage_path()
            if path: return path
    except: pass
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = get_data_dir()
DB_FILE = os.path.join(DATA_DIR, "spark_billing.db")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

def init_db():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                id TEXT PRIMARY KEY,
                date TEXT,
                name TEXT,
                items_details TEXT,
                month TEXT,
                year TEXT,
                total REAL,
                advance REAL DEFAULT 0,
                remarks TEXT
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database init error: {e}")

def load_settings():
    default = {
        "logo_width": 30, "watermark_width": 120, "watermark_alpha": 0.15,
        "signature_width": 24, "signature_box_width": 50,
        "signature_offset_x": 0, "signature_offset_y": 18,
        "printer_type": "None", "printer_ip": "192.168.1.100", "printer_port": 9100,
        "printer_bt_mac": ""
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data: default.update(data)
        except: pass
    return default

def save_settings(data):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except: pass

# --- Printer Logic (ESC/POS) ---
class POSPrinter:
    @staticmethod
    def print_invoice(inv_id, name, month, year, items, total, settings):
        p_type = settings.get("printer_type", "None")
        if p_type == "None": return
        
        # Build ESC/POS Data
        receipt = b"\x1b\x40" # Initialize
        receipt += b"\x1b\x61\x01" # Center
        receipt += b"THE SPARK EDUCATION CENTRE\n"
        receipt += b"Invoice Billing System\n\n"
        
        receipt += b"\x1b\x61\x00" # Left
        receipt += f"Inv ID: {inv_id}\n".encode('utf-8')
        receipt += f"Date: {datetime.now().strftime('%d-%m-%Y')}\n".encode('utf-8')
        receipt += f"Name: {name}\n".encode('utf-8')
        receipt += f"Period: {month} {year}\n".encode('utf-8')
        receipt += b"--------------------------------\n"
        
        for it in items:
            desc = it['desc'][:20]
            amt = f"{(it['amt']-it['adv']):,.0f}"
            receipt += f"{desc:<22}{amt:>10}\n".encode('utf-8')
            
        receipt += b"--------------------------------\n"
        receipt += f"TOTAL: {total:,.0f} MMK\n\n".encode('utf-8')
        receipt += b"\x1b\x61\x01" # Center
        receipt += b"Thank You!\n\n\n\n\n\x1d\x56\x00" # Cut
        
        if p_type == "WiFi":
            threading.Thread(target=POSPrinter._send_wifi, args=(settings['printer_ip'], settings['printer_port'], receipt)).start()
        elif p_type == "Bluetooth" and platform == 'android':
            threading.Thread(target=POSPrinter._send_bluetooth, args=(settings['printer_bt_mac'], receipt)).start()

    @staticmethod
    def _send_wifi(ip, port, data):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5)
                s.connect((ip, int(port)))
                s.sendall(data)
        except Exception as e: print(f"WiFi Print Error: {e}")

    @staticmethod
    def _send_bluetooth(mac, data):
        try:
            from jnius import autoclass
            BluetoothAdapter = autoclass('android.bluetooth.BluetoothAdapter')
            UUID = autoclass('java.util.UUID')
            adapter = BluetoothAdapter.getDefaultAdapter()
            device = adapter.getRemoteDevice(mac)
            socket_bt = device.createRfcommSocketToServiceRecord(UUID.fromString("00001101-0000-1000-8000-00805F9B34FB"))
            socket_bt.connect()
            ostream = socket_bt.getOutputStream()
            ostream.write(data)
            ostream.flush()
            socket_bt.close()
        except Exception as e: print(f"BT Print Error: {e}")

def calculate_verification_hash(inv_id):
    SECRET_SALT = "SPARK_SECURE_2026"
    secure_str = f"{inv_id}{SECRET_SALT}"
    return hashlib.sha256(secure_str.encode()).hexdigest()[:12].upper()

def generate_invoice_id():
    return datetime.now().strftime("%Y%m%d%H%M%S")

def resolve_image(candidates):
    for n in candidates:
        p = os.path.join(BASE_DIR, n)
        if os.path.exists(p): return p
    return None

LOGO_FILE = resolve_image(["logo.png", "Logo.png", "logo.jpg"])
SIGN_FILE = resolve_image(["signature.png", "DigitalSignature.png"])

class InvoicePDF(FPDF):
    def header(self):
        if LOGO_FILE and os.path.exists(LOGO_FILE):
            self.image(LOGO_FILE, 10, 8, 30)
        self.set_font('Arial', 'B', 15)
        self.cell(80)
        self.cell(30, 10, 'THE SPARK EDUCATION CENTRE', 0, 0, 'C')
        self.ln(20)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', 0, 0, 'C')

def generate_pdf_invoice(inv_id, name, month, year, items, total, settings):
    pdf = InvoicePDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font('Times', '', 12)
    
    pdf.cell(0, 10, f'Invoice ID: {inv_id}', 0, 1)
    pdf.cell(0, 10, f'Date: {datetime.now().strftime("%Y-%m-%d")}', 0, 1)
    pdf.cell(0, 10, f'Student Name: {name}', 0, 1)
    pdf.cell(0, 10, f'Billing Period: {month} {year}', 0, 1)
    pdf.ln(10)
    
    # Table
    pdf.set_fill_color(200, 220, 255)
    pdf.cell(140, 10, 'Description', 1, 0, 'C', 1)
    pdf.cell(50, 10, 'Amount (MMK)', 1, 1, 'C', 1)
    
    for it in items:
        pdf.cell(140, 10, it['desc'], 1)
        pdf.cell(50, 10, f"{it['amt']-it['adv']:,.0f}", 1, 1, 'R')
        
    pdf.ln(5)
    pdf.set_font('Times', 'B', 14)
    pdf.cell(140, 10, 'GRAND TOTAL:', 0, 0, 'R')
    pdf.cell(50, 10, f"{total:,.0f} MMK", 0, 1, 'R')
    
    # Save Path
    filename = f"Spark_Invoice_{inv_id}.pdf"
    if platform == "android":
        from android.storage import primary_external_storage_path
        storage = primary_external_storage_path()
        out_dir = os.path.join(storage, "Documents", "SparkInvoices")
        if not os.path.exists(out_dir): os.makedirs(out_dir)
        out_path = os.path.join(out_dir, filename)
    else:
        out_path = os.path.join(os.path.expanduser("~"), filename)
        
    pdf.output(out_path)
    return out_path

def generate_image_invoice(inv_id, name, month, year, items, total):
    if not PILImage: return None
    
    # Setup Dimensions
    width, height = 800, 1100
    img = PILImage.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    
    # Use default fonts
    font_bold = ImageFont.load_default()
    font_reg = ImageFont.load_default()

    # Draw Header
    draw.rectangle([0, 0, width, 120], fill=(20, 40, 120))
    draw.text((width//2, 40), "THE SPARK EDUCATION CENTRE", fill=(255, 255, 255), anchor="mm")
    draw.text((width//2, 80), "Invoice Billing Management System", fill=(240, 240, 240), anchor="mm")

    # Invoice Info
    y = 150
    draw.text((50, y), f"Invoice ID: {inv_id}", fill=(0, 0, 0))
    draw.text((width-250, y), f"Date: {datetime.now().strftime('%d-%m-%Y')}", fill=(0, 0, 0))
    
    y += 40
    draw.text((50, y), f"Student Name: {name or 'N/A'}", fill=(0, 0, 0))
    draw.text((width-250, y), f"Period: {month} {year}", fill=(0, 0, 0))

    # Table Header
    y += 60
    draw.rectangle([40, y, width-40, y+40], fill=(240, 240, 240))
    draw.text((60, y+10), "Description", fill=(0, 0, 0))
    draw.text((width-150, y+10), "Amount", fill=(0, 0, 0))
    
    # Table Items
    y += 50
    for it in items:
        amt = it['amt'] - it['adv']
        draw.text((60, y), it['desc'], fill=(0, 0, 0))
        draw.text((width-150, y), f"{amt:,.0f}", fill=(0, 0, 0))
        y += 35
        if y > height - 250: break

    # Total
    y += 20
    draw.line([40, y, width-40, y], fill=(100, 100, 100), width=2)
    y += 20
    draw.text((width-300, y), "GRAND TOTAL:", fill=(0, 0, 0))
    draw.text((width-150, y), f"{total:,.0f} MMK", fill=(20, 100, 20))

    # Verification
    v_hash = calculate_verification_hash(inv_id)
    draw.text((50, height-150), f"Verification: {v_hash}", fill=(150, 150, 150))
    
    if SIGN_FILE and os.path.exists(SIGN_FILE):
        try:
            sig = PILImage.open(SIGN_FILE).convert("RGBA")
            sig.thumbnail((150, 100))
            img.paste(sig, (width-200, height-180), sig)
        except: pass
    
    draw.text((width-200, height-60), "Authorized Signature", fill=(0, 0, 0))

    # Save Path
    filename = f"Spark_Invoice_{inv_id}.jpg"
    if platform == "android":
        from android.storage import primary_external_storage_path
        storage = primary_external_storage_path()
        out_dir = os.path.join(storage, "Pictures", "SparkInvoices")
        if not os.path.exists(out_dir): os.makedirs(out_dir)
        out_path = os.path.join(out_dir, filename)
        img.save(out_path, "JPEG", quality=95)
        # Scan file logic would go here if called from History
    else:
        out_path = os.path.join(os.path.expanduser("~"), filename)
        img.save(out_path, "JPEG", quality=95)
        
    return out_path

def scan_file_android(path):
    try:
        from jnius import autoclass
        Context = autoclass('android.content.Context')
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        MediaScannerConnection = autoclass('android.media.MediaScannerConnection')
        
        activity = PythonActivity.mActivity
        MediaScannerConnection.scanFile(
            activity, 
            [path], 
            ["image/jpeg"], 
            None
        )
    except Exception as e:
        print(f"Gallery scan failed: {e}")

# --- Screens ---
class CreateInvoiceScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.settings = load_settings()
        self.current_items = []
        self.selected_item_idx = None
        self.build_ui()

    def build_ui(self):
        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        
        # --- App Bar Header (Pro Look) ---
        app_bar = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(100), spacing=dp(5), padding=[0, dp(10)])
        with app_bar.canvas.before:
            Color(0.1, 0.3, 0.7, 1) # Primary Blue
            self.header_bg = Rectangle(size=app_bar.size, pos=app_bar.pos)
        app_bar.bind(size=self._update_header_bg, pos=self._update_header_bg)

        title = Label(text="THE SPARK EDUCATION CENTRE", font_size=dp(20), bold=True, color=(1,1,1,1))
        app_bar.add_widget(title)

        header = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(5), padding=[dp(5), 0])
        btn_hist = Button(text="HISTORY", font_size=dp(12), background_normal='', background_color=(1,1,1,0.2), on_press=self.go_to_history)
        btn_verf = Button(text="VERIFY", font_size=dp(12), background_normal='', background_color=(1,1,1,0.2), on_press=lambda x: App.get_running_app().show_verify_popup())
        btn_them = Button(text="THEME", font_size=dp(12), background_normal='', background_color=(1,1,1,0.2), on_press=lambda x: App.get_running_app().toggle_theme())
        btn_sett = Button(text="SETTINGS", font_size=dp(12), background_normal='', background_color=(1,1,1,0.2), on_press=lambda x: App.get_running_app().show_settings_popup())
        
        header.add_widget(btn_hist); header.add_widget(btn_verf); header.add_widget(btn_them); header.add_widget(btn_sett)
        app_bar.add_widget(header)
        layout.add_widget(app_bar)

        scroll = ScrollView()
        form = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(10), padding=dp(5))
        form.bind(minimum_height=form.setter('height'))

        # Section Header
        form.add_widget(Label(text="[b]1. Create Invoice[/b]", markup=True, font_size=dp(16), color=(0.1, 0.1, 0.2, 1), size_hint_y=None, height=dp(35), halign='left'))
        
        # Student Name Card-like container
        name_box = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(70), spacing=dp(5))
        name_box.add_widget(Label(text="Student Name", size_hint_y=None, height=dp(20), color=(0.4, 0.4, 0.5, 1), halign='left'))
        self.ent_name = TextInput(hint_text="Enter student name", multiline=False, size_hint_y=None, height=dp(45), padding=[dp(10), dp(10)])
        name_box.add_widget(self.ent_name)
        form.add_widget(name_box)

        # Billing Period
        form.add_widget(Label(text="Billing Period", size_hint_y=None, height=dp(20), color=(0.4, 0.4, 0.5, 1), halign='left'))
        period = GridLayout(cols=2, size_hint_y=None, height=dp(50), spacing=dp(10))
        self.ent_month = Spinner(text=datetime.now().strftime("%B"), values=["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"])
        self.ent_year = Spinner(text=str(datetime.now().year), values=[str(y) for y in range(2026, 2046)])
        period.add_widget(self.ent_month)
        period.add_widget(self.ent_year)
        form.add_widget(period)

        # Category & Level
        cat_lvl = GridLayout(cols=2, size_hint_y=None, height=dp(70), spacing=dp(10))
        cat_box = BoxLayout(orientation='vertical', spacing=dp(2))
        cat_box.add_widget(Label(text="Category", color=(0.4, 0.4, 0.5, 1), size_hint_y=None, height=dp(20)))
        self.ent_cat = Spinner(text="Course Fee", values=[" ", "Course Fee","Tutoring Class Fee","Exam Book Fee","Exam Practice Book Fee","Textbook Fee", "Exam Registration Fee"])
        cat_box.add_widget(self.ent_cat)
        
        lvl_box = BoxLayout(orientation='vertical', spacing=dp(2))
        lvl_box.add_widget(Label(text="Level/Class", color=(0.4, 0.4, 0.5, 1), size_hint_y=None, height=dp(20)))
        self.ent_sub = Spinner(text="Phonics", values=[" ", "Phonics", "STARTERS", "MOVERS", "FLYERS", "KET", "PET", "B1", "B2", "Maths", "Physics", "YEAR 1", "YEAR 2", "YEAR 3", "YEAR 4", "YEAR 5", "YEAR 6", "YEAR 7", "YEAR 8", "YEAR 9", "YEAR 10"])
        lvl_box.add_widget(self.ent_sub)
        
        cat_lvl.add_widget(cat_box); cat_lvl.add_widget(lvl_box)
        form.add_widget(cat_lvl)

        # Details
        det_box = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(70), spacing=dp(5))
        det_box.add_widget(Label(text="Details (Optional)", color=(0.4, 0.4, 0.5, 1), size_hint_y=None, height=dp(20)))
        self.ent_details = TextInput(hint_text="Additional details...", multiline=False, size_hint_y=None, height=dp(45), padding=[dp(10), dp(10)])
        det_box.add_widget(self.ent_details)
        form.add_widget(det_box)

        # Amount & Advance
        amt_adv = GridLayout(cols=2, size_hint_y=None, height=dp(70), spacing=dp(10))
        amt_box = BoxLayout(orientation='vertical', spacing=dp(2))
        amt_box.add_widget(Label(text="Amount (MMK)", color=(0.4, 0.4, 0.5, 1), size_hint_y=None, height=dp(20)))
        self.ent_amt = TextInput(text="0", multiline=False, input_filter='float', size_hint_y=None, height=dp(45))
        amt_box.add_widget(self.ent_amt)
        
        adv_box = BoxLayout(orientation='vertical', spacing=dp(2))
        adv_box.add_widget(Label(text="Advance (MMK)", color=(0.4, 0.4, 0.5, 1), size_hint_y=None, height=dp(20)))
        self.ent_adv = TextInput(text="0", multiline=False, input_filter='float', size_hint_y=None, height=dp(45))
        adv_box.add_widget(self.ent_adv)
        
        amt_adv.add_widget(amt_box); amt_adv.add_widget(adv_box)
        form.add_widget(amt_adv)

        form.add_widget(Button(text="+ ADD TO LIST", size_hint_y=None, height=dp(55), background_normal='', background_color=(0.15, 0.4, 0.9, 1), bold=True, font_size=dp(16), on_press=self.add_item))

        # Current Items List
        form.add_widget(Label(text="Current Invoice Items", bold=True, size_hint_y=None, height=dp(40), color=(0.2, 0.4, 0.8, 1)))
        
        self.items_layout = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(8), padding=[dp(5), 0])
        self.items_layout.bind(minimum_height=self.items_layout.setter('height'))
        
        # Premium Frame for items
        items_frame = BoxLayout(orientation='vertical', size_hint_y=None, padding=dp(2))
        with items_frame.canvas.before:
            Color(1, 1, 1, 1) # Card White
            self.items_bg = RoundedRectangle(size=items_frame.size, pos=items_frame.pos, radius=[dp(12),])
        items_frame.bind(size=self._update_items_bg, pos=self._update_items_bg)
        items_frame.add_widget(self.items_layout)
        form.add_widget(items_frame)

        # Bottom Action Bar
        bottom_actions = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(200), spacing=dp(10), padding=dp(5))
        
        total_box = BoxLayout(size_hint_y=None, height=dp(50), padding=[dp(15), 0])
        with total_box.canvas.before:
            Color(0.05, 0.7, 0.3, 0.1)
            self.total_bg = RoundedRectangle(size=total_box.size, pos=total_box.pos, radius=[dp(12),])
        total_box.bind(size=self._update_total_bg, pos=self._update_total_bg)
        
        self.lbl_total = Label(text="TOTAL: 0 MMK", font_size=dp(22), bold=True, color=(0.05, 0.5, 0.2, 1), halign='right')
        total_box.add_widget(self.lbl_total)
        bottom_actions.add_widget(total_box)
        
        btn_finalize = Button(text="FINALIZE & PRINT INVOICE", size_hint_y=None, height=dp(65), background_normal='', background_color=(0.1, 0.7, 0.3, 1), bold=True, font_size=dp(18))
        btn_finalize.bind(on_press=self.finalize_invoice)
        bottom_actions.add_widget(btn_finalize)
        
        btn_clear = Button(text="CLEAR ALL ITEMS", size_hint_y=None, height=dp(45), background_normal='', background_color=(0.5, 0.5, 0.6, 1), bold=True)
        btn_clear.bind(on_press=self.clear_all)
        bottom_actions.add_widget(btn_clear)
        form.add_widget(bottom_actions)

        scroll.add_widget(form)
        layout.add_widget(scroll)
        self.add_widget(layout)

    def _update_header_bg(self, instance, value):
        self.header_bg.pos = instance.pos
        self.header_bg.size = instance.size

    def _update_items_bg(self, instance, value):
        self.items_bg.pos = instance.pos
        self.items_bg.size = instance.size

    def _update_total_bg(self, instance, value):
        self.total_bg.pos = instance.pos
        self.total_bg.size = instance.size

    def add_item(self, instance):
        try:
            amt = float(self.ent_amt.text or 0)
            adv = float(self.ent_adv.text or 0)
        except ValueError: return
        
        if amt <= 0: return
        
        desc = f"{self.ent_cat.text} ({self.ent_sub.text.strip()})"
        if self.ent_details.text.strip(): desc = f"{self.ent_details.text.strip()} - {desc}"
        
        item = {"desc": desc, "amt": amt, "adv": adv, "cat": self.ent_cat.text, "sub": self.ent_sub.text, "det": self.ent_details.text}
        self.current_items.append(item)
        self.refresh_list()
        self.ent_amt.text = "0"; self.ent_adv.text = "0"; self.ent_details.text = ""

    def refresh_list(self):
        self.items_layout.clear_widgets()
        total = 0
        for i, it in enumerate(self.current_items):
            row = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(5))
            
            # Item Info
            info = Button(text=f"{it['desc']} | {it['amt']-it['adv']:,.0f} MMK", size_hint_x=0.7, background_color=(1,1,1,1), color=(0,0,0,1), halign='left', valign='middle')
            info.bind(size=info.setter('text_size'))
            row.add_widget(info)
            
            # Edit Button
            edit_btn = Button(text="Edit", size_hint_x=0.15, background_color=(0.2, 0.6, 0.9, 1))
            edit_btn.bind(on_press=lambda x, idx=i: self.edit_item(idx))
            row.add_widget(edit_btn)
            
            # Delete Button
            del_btn = Button(text="Del", size_hint_x=0.15, background_color=(0.9, 0.3, 0.2, 1))
            del_btn.bind(on_press=lambda x, idx=i: self.delete_item(idx))
            row.add_widget(del_btn)
            
            self.items_layout.add_widget(row)
            total += (it['amt'] - it['adv'])
        self.lbl_total.text = f"TOTAL: {total:,.0f} MMK"

    def edit_item(self, idx):
        it = self.current_items.pop(idx)
        self.ent_cat.text = it['cat']; self.ent_sub.text = it['sub']; self.ent_details.text = it['det']
        self.ent_amt.text = str(it['amt']); self.ent_adv.text = str(it['adv'])
        self.refresh_list()

    def delete_item(self, idx):
        self.current_items.pop(idx)
        self.refresh_list()

    def clear_all(self, instance):
        self.current_items = []; self.ent_name.text = ""
        self.refresh_list()

    def finalize_invoice(self, instance):
        if not self.current_items:
            Popup(title="Error", content=Label(text="No items in invoice!"), size_hint=(0.8, 0.4)).open()
            return
            
        inv_id = generate_invoice_id()
        name, month, year = self.ent_name.text.strip(), self.ent_month.text, self.ent_year.text
        
        # Calculate Total
        total = sum(it['amt'] - it['adv'] for it in self.current_items)
        
        # 1. Save to Database
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            items_str = json.dumps(self.current_items)
            cursor.execute("INSERT INTO invoices (id, date, name, items_details, month, year, total) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (inv_id, datetime.now().strftime("%Y-%m-%d"), name, items_str, month, year, total))
            conn.commit()
            conn.close()
        except Exception as e: print(f"DB Save Error: {e}")

        # 2. Save Image & PDF
        img_path = generate_image_invoice(inv_id, name, month, year, self.current_items, total)
        pdf_path = generate_pdf_invoice(inv_id, name, month, year, self.current_items, total, self.settings)
        
        if platform == "android" and img_path: scan_file_android(img_path)
        
        # 3. Print to POS Printer (WiFi/BT)
        POSPrinter.print_invoice(inv_id, name, month, year, self.current_items, total, self.settings)
        
        msg = f"Invoice #{inv_id} saved!\n"
        if img_path: msg += f"Image & PDF saved to Gallery/Documents.\n"
        if self.settings.get("printer_type") != "None": msg += f"Sent to {self.settings['printer_type']} Printer."
            
        Popup(title="Success", content=Label(text=msg, halign='center'), size_hint=(0.9, 0.4)).open()
        self.clear_all(None)

    def go_to_history(self, instance): self.manager.current = 'history'

class HistoryScreen(Screen):
    def on_enter(self): self.build_ui()
    def build_ui(self):
        self.clear_widgets()
        layout = BoxLayout(orientation='vertical', padding=0, spacing=0)
        
        # --- App Bar ---
        app_bar = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(110), spacing=dp(5), padding=[0, dp(10)])
        with app_bar.canvas.before:
            Color(0.1, 0.3, 0.7, 1)
            self.header_bg = Rectangle(size=app_bar.size, pos=app_bar.pos)
        app_bar.bind(size=self._update_header_bg, pos=self._update_header_bg)

        top_row = BoxLayout(size_hint_y=None, height=dp(40), padding=[dp(10), 0], spacing=dp(10))
        btn_back = Button(text="← BACK", size_hint_x=0.25, background_normal='', background_color=(1,1,1,0.2), bold=True)
        btn_back.bind(on_press=lambda x: setattr(self.manager, 'current', 'create'))
        top_row.add_widget(btn_back)
        
        title = Label(text="RECORDS HISTORY", font_size=dp(18), bold=True, color=(1,1,1,1))
        top_row.add_widget(title)
        app_bar.add_widget(top_row)

        self.search_input = TextInput(hint_text="Search Name or ID...", multiline=False, size_hint_y=None, height=dp(45), padding=[dp(15), dp(10)], background_normal='', background_color=(1,1,1,0.9))
        self.search_input.bind(text=self.filter_list)
        app_bar.add_widget(BoxLayout(padding=[dp(15), 0], size_hint_y=None, height=dp(45)).add_widget(self.search_input) or self.search_input)
        layout.add_widget(app_bar)

        # Table Header
        t_header = BoxLayout(size_hint_y=None, height=dp(35), padding=[dp(15), 0])
        with t_header.canvas.before:
            Color(0.9, 0.9, 0.95, 1)
            Rectangle(size=t_header.size, pos=t_header.pos)
        t_header.add_widget(Label(text="ID / NAME", color=(0.4, 0.4, 0.5, 1), font_size=dp(11), bold=True, halign='left'))
        t_header.add_widget(Label(text="TOTAL", color=(0.4, 0.4, 0.5, 1), font_size=dp(11), bold=True, halign='right'))
        layout.add_widget(t_header)

        # List
        scroll = ScrollView(bar_width=dp(4))
        self.list_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(1))
        self.list_box.bind(minimum_height=self.list_box.setter('height'))
        
        self.load_history()
        
        scroll.add_widget(self.list_box)
        layout.add_widget(scroll)
        
        self.add_widget(layout)

    def _update_header_bg(self, instance, value):
        self.header_bg.pos = instance.pos
        self.header_bg.size = instance.size

    def filter_list(self, instance, text):
        query = text.lower().strip()
        for child in self.list_box.children[:]:
            # Each child is a Button (main_btn)
            # Its first child is a BoxLayout (btn_layout)
            # Its first child is a BoxLayout (info_v)
            # info_v.children[0] is name, [1] is ID (added in reverse order)
            try:
                btn_layout = child.children[0]
                info_v = btn_layout.children[1]
                inv_id_lbl = info_v.children[1]
                name_lbl = info_v.children[0]
                
                match = query in inv_id_lbl.text.lower() or query in name_lbl.text.lower()
                child.size_hint_y = 1 if match else 0
                child.opacity = 1 if match else 0
                child.height = dp(75) if match else 0
            except: pass

    def load_history(self):
        self.list_box.clear_widgets()
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, total, items_details, month, year FROM invoices ORDER BY id DESC")
            rows = cursor.fetchall()
            conn.close()
            
            for r in rows:
                inv_id, name, total, items_json, month, year = r
                row = BoxLayout(size_hint_y=None, height=dp(75), spacing=dp(5), padding=[dp(10), dp(5)])
                
                # Main info button (75% width)
                main_btn = Button(background_normal='', background_color=(1,1,1,1), size_hint_x=0.75)
                main_btn.bind(on_press=lambda x, d=r: self.show_preview(d))
                
                btn_layout = BoxLayout(spacing=dp(5))
                info_v = BoxLayout(orientation='vertical')
                info_v.add_widget(Label(text=f"{inv_id}", color=(0.5, 0.5, 0.6, 1), font_size=dp(10), halign='left', valign='middle'))
                info_v.add_widget(Label(text=f"{name}", color=(0.1, 0.1, 0.2, 1), font_size=dp(15), bold=True, halign='left', valign='middle'))
                for lbl in info_v.children: lbl.bind(size=lbl.setter('text_size'))
                btn_layout.add_widget(info_v)
                
                val_v = BoxLayout(orientation='vertical', size_hint_x=0.45)
                val_v.add_widget(Label(text=f"{total:,.0f}", color=(0.05, 0.6, 0.2, 1), font_size=dp(16), bold=True, halign='right', valign='middle'))
                val_v.add_widget(Label(text="MMK", color=(0.5, 0.5, 0.6, 1), font_size=dp(10), halign='right', valign='middle'))
                for lbl in val_v.children: lbl.bind(size=lbl.setter('text_size'))
                btn_layout.add_widget(val_v)
                
                main_btn.add_widget(btn_layout)
                row.add_widget(main_btn)
                
                # POS Print Button (25% width)
                btn_pos = Button(text="PRINT\nPOS", size_hint_x=0.25, background_normal='', background_color=(0.1, 0.6, 0.2, 1), font_size=dp(11), bold=True)
                items = json.loads(items_json)
                btn_pos.bind(on_press=lambda x, i=inv_id, n=name, m=month, y=year, its=items, t=total: 
                             POSPrinter.print_invoice(i, n, m, y, its, t, App.get_running_app().settings))
                row.add_widget(btn_pos)
                
                self.list_box.add_widget(row)
        except Exception as e: print(f"Load history error: {e}")

    def show_preview(self, data):
        inv_id, name, total, items_json, month, year = data
        items = json.loads(items_json)
        
        content = BoxLayout(orientation='vertical', padding=dp(15), spacing=dp(15))
        with content.canvas.before:
            Color(0.95, 0.96, 0.97, 1)
            Rectangle(size=content.size, pos=content.pos)

        # Invoice Card
        card = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(10), size_hint_y=None, height=dp(280))
        with card.canvas.before:
            Color(1, 1, 1, 1)
            self.rect = RoundedRectangle(size=card.size, pos=card.pos, radius=[dp(20),])
        card.bind(size=self._update_rect, pos=self._update_rect)
        
        card.add_widget(Label(text="INVOICE DETAILS", color=(0.2, 0.4, 0.8, 1), bold=True, font_size=dp(14)))
        card.add_widget(Label(text=f"ID: #{inv_id}", color=(0.5, 0.5, 0.6, 1), font_size=dp(12)))
        card.add_widget(Label(text=f"{name}", color=(0.1, 0.1, 0.2, 1), bold=True, font_size=dp(20)))
        card.add_widget(Label(text=f"Period: {month} {year}", color=(0.4, 0.4, 0.5, 1)))
        
        card.add_widget(Label(text=f"{total:,.0f}", color=(0.05, 0.6, 0.2, 1), bold=True, font_size=dp(32)))
        card.add_widget(Label(text="TOTAL MMK", color=(0.5, 0.5, 0.6, 1), font_size=dp(12)))
        
        content.add_widget(card)
        
        # Action Buttons (Pro Style)
        actions = GridLayout(cols=1, spacing=dp(10), size_hint_y=None, height=dp(220))
        
        btn_pos = Button(text="PRINT POS RECEIPT", background_normal='', background_color=(0.1, 0.7, 0.3, 1), bold=True, height=dp(55), size_hint_y=None)
        btn_pos.bind(on_press=lambda x: POSPrinter.print_invoice(inv_id, name, month, year, items, total, App.get_running_app().settings))
        actions.add_widget(btn_pos)

        btn_pdf = Button(text="REPRINT PDF INVOICE", background_normal='', background_color=(0.15, 0.4, 0.9, 1), bold=True, height=dp(55), size_hint_y=None)
        btn_pdf.bind(on_press=lambda x: generate_pdf_invoice(inv_id, name, month, year, items, total, App.get_running_app().settings))
        actions.add_widget(btn_pdf)

        btn_img = Button(text="SHARE AS IMAGE", background_normal='', background_color=(0.95, 0.6, 0.1, 1), bold=True, height=dp(55), size_hint_y=None)
        btn_img.bind(on_press=lambda x: scan_file_android(generate_image_invoice(inv_id, name, month, year, items, total)) if platform == "android" else generate_image_invoice(inv_id, name, month, year, items, total))
        actions.add_widget(btn_img)

        btn_close = Button(text="CLOSE PREVIEW", background_normal='', background_color=(0.5, 0.5, 0.6, 1), bold=True, height=dp(45), size_hint_y=None)
        actions.add_widget(btn_close)
        
        content.add_widget(actions)
        
        popup = Popup(title="Mobile Invoice View", content=content, size_hint=(0.95, 0.9), background_color=(0,0,0,0.8))
        btn_close.bind(on_press=popup.dismiss)
        popup.open()

    def _update_rect(self, instance, value):
        self.rect.pos = instance.pos
        self.rect.size = instance.size

class BillingApp(App):
    def build(self):
        init_db() # Initialize DB at startup
        Window.clearcolor = (0.94, 0.95, 0.96, 1)
        if platform == 'android':
            from android.permissions import request_permissions, Permission
            request_permissions([Permission.WRITE_EXTERNAL_STORAGE, Permission.READ_EXTERNAL_STORAGE, Permission.MANAGE_EXTERNAL_STORAGE])
        
        sm = ScreenManager()
        sm.add_widget(CreateInvoiceScreen(name='create'))
        sm.add_widget(HistoryScreen(name='history'))
        return sm
    def show_settings_popup(self):
        content = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        
        # Printer Config
        content.add_widget(Label(text="POS Printer Settings", bold=True, size_hint_y=None, height=dp(30)))
        
        grid = GridLayout(cols=2, spacing=dp(5), size_hint_y=None, height=dp(150))
        grid.add_widget(Label(text="Type:"))
        p_type = Spinner(text=self.settings.get("printer_type", "None"), values=["None", "WiFi", "Bluetooth"])
        grid.add_widget(p_type)
        
        grid.add_widget(Label(text="WiFi IP:"))
        p_ip = TextInput(text=self.settings.get("printer_ip", "192.168.1.100"), multiline=False)
        grid.add_widget(p_ip)
        
        grid.add_widget(Label(text="BT MAC:"))
        p_mac = TextInput(text=self.settings.get("printer_bt_mac", ""), multiline=False, hint_text="00:11:22:33:44:55")
        grid.add_widget(p_mac)
        content.add_widget(grid)
        
        btn_save = Button(text="SAVE SETTINGS", size_hint_y=None, height=dp(50), background_color=(0.1, 0.5, 0.1, 1))
        content.add_widget(btn_save)
        
        popup = Popup(title="Settings", content=content, size_hint=(0.9, 0.6))
        
        def save_all(instance):
            self.settings.update({
                "printer_type": p_type.text,
                "printer_ip": p_ip.text,
                "printer_bt_mac": p_mac.text
            })
            save_settings(self.settings)
            popup.dismiss()
            
        btn_save.bind(on_press=save_all)
        popup.open()

    def show_verify_popup(self):
        # Existing verify logic or stub
        Popup(title="Verify", content=Label(text="Verification system ready."), size_hint=(0.7, 0.3)).open()

    def toggle_theme(self):
        # Theme toggle stub
        pass

if __name__ == '__main__':
    BillingApp().run()
