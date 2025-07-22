import os
import sqlite3
from datetime import datetime, timedelta
import logging
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_wtf.csrf import CSRFProtect
from contextlib import closing
from flask_login import LoginManager, login_user, login_required, logout_user, UserMixin, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from whatsapp_gonder import whatsapp_mesaj_gonder
import threading
import time

# Uygulama sürüm bilgisi
SURUM = "1.0.0"

# Logging ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('kuafor_app.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class Kullanici(UserMixin):
    def __init__(self, id, kullanici_adi, sifre_hash, rol):
        self.id = id
        self.kullanici_adi = kullanici_adi
        self.sifre_hash = sifre_hash
        self.rol = rol

    @staticmethod
    def get_by_username(username):
        with closing(get_db_connection()) as conn:
            row = conn.execute("SELECT * FROM kullanicilar WHERE kullanici_adi = ?", (username,)).fetchone()
            if row:
                return Kullanici(row['id'], row['kullanici_adi'], row['sifre_hash'], row['rol'])
        return None

    @staticmethod
    def get_by_id(user_id):
        with closing(get_db_connection()) as conn:
            row = conn.execute("SELECT * FROM kullanicilar WHERE id = ?", (user_id,)).fetchone()
            if row:
                return Kullanici(row['id'], row['kullanici_adi'], row['sifre_hash'], row['rol'])
        return None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "kuaför_uygulaması_gizli_anahtar")
csrf = CSRFProtect(app)
app.jinja_env.globals['datetime'] = datetime

# Açık rotalar listesi (login gerektirmeyen rotalar)
ACIK_ROTALAR = [
    'online_randevu',  # Online randevu sayfası
    'hizmet_sorumlu_calisanlar',  # Online randevu için API
    'dolu_saatler',  # Online randevu için API
    'login',  # Giriş sayfası
    'static',  # Statik dosyalar
    'randevu_basarili',  # Randevu başarılı sayfası
    'yedek_yukle'  # Yedek yükleme sayfası (admin kontrolü içinde yapılıyor)
]

# Tüm rotaları otomatik olarak korumak için before_request
@app.before_request
def koruma():
    # Mevcut endpoint'i al
    endpoint = request.endpoint
    
    # Eğer endpoint None ise veya açık rotalarda ise, devam et
    if endpoint is None or any(endpoint.endswith(rota) for rota in ACIK_ROTALAR):
        return
    
    # Kullanıcı giriş yapmamışsa, login sayfasına yönlendir
    if not current_user.is_authenticated:
        return redirect(url_for('login', next=request.url))


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Lütfen giriş yapınız."

@login_manager.user_loader
def load_user(user_id):
    return Kullanici.get_by_id(user_id)

def get_db_connection():
    conn = sqlite3.connect('kuaför.db')
    conn.row_factory = sqlite3.Row
    return conn

def get_whatsapp_sablon():
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT whatsapp_sablon FROM bildirim_ayarlar ORDER BY id DESC LIMIT 1").fetchone()
        return row['whatsapp_sablon'] if row and row['whatsapp_sablon'] else None

def set_whatsapp_sablon(sablon):
    with closing(get_db_connection()) as conn:
        # Eğer hiç kayıt yoksa ekle, varsa güncelle
        row = conn.execute("SELECT id FROM bildirim_ayarlar ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            conn.execute("UPDATE bildirim_ayarlar SET whatsapp_sablon = ? WHERE id = ?", (sablon, row['id']))
        else:
            conn.execute("INSERT INTO bildirim_ayarlar (whatsapp_sablon) VALUES (?)", (sablon,))
        conn.commit()

def get_online_whatsapp_sablon():
    try:
        with closing(get_db_connection()) as conn:
            try:
                # Önce sütunun var olup olmadığını kontrol et
                conn.execute("SELECT online_whatsapp_sablon FROM bildirim_ayarlar LIMIT 1")
                # Sütun varsa, değeri al
                row = conn.execute("SELECT online_whatsapp_sablon FROM bildirim_ayarlar ORDER BY id DESC LIMIT 1").fetchone()
                return row['online_whatsapp_sablon'] if row and row['online_whatsapp_sablon'] else None
            except sqlite3.OperationalError:
                # Sütun yoksa None döndür
                return None
    except Exception as e:
        logger.error(f"Online WhatsApp şablonu alınırken hata: {str(e)}")
        return None
        
def generate_randevu_saatleri():
    """Sistem ayarlarına göre randevu saatlerini otomatik oluşturur"""
    try:
        with closing(get_db_connection()) as conn:
            # Çalışma saatleri ve randevu aralığını al
            calisma_baslangic_row = conn.execute("SELECT deger FROM sistem_ayarlar WHERE anahtar = 'calisma_baslangic'").fetchone()
            calisma_bitis_row = conn.execute("SELECT deger FROM sistem_ayarlar WHERE anahtar = 'calisma_bitis'").fetchone()
            randevu_araligi_row = conn.execute("SELECT deger FROM sistem_ayarlar WHERE anahtar = 'randevu_araligi'").fetchone()
            
            calisma_baslangic = calisma_baslangic_row['deger'] if calisma_baslangic_row else '09:00'
            calisma_bitis = calisma_bitis_row['deger'] if calisma_bitis_row else '18:00'
            randevu_araligi = int(randevu_araligi_row['deger']) if randevu_araligi_row else 30
            
            # Randevu saatlerini otomatik oluştur
            randevu_saatleri = []
            try:
                baslangic = datetime.strptime(calisma_baslangic, '%H:%M')
                bitis = datetime.strptime(calisma_bitis, '%H:%M')
                
                # Bitiş saatinden randevu aralığı kadar önce son randevu olabilir
                son_randevu_saati = bitis - timedelta(minutes=1)
                
                current = baslangic
                while current < son_randevu_saati:
                    randevu_saatleri.append(current.strftime('%H:%M'))
                    current = current + timedelta(minutes=randevu_araligi)
                    
                    # Eğer bir sonraki randevu bitiş saatini geçiyorsa döngüyü sonlandır
                    if current >= bitis:
                        break
            except Exception as e:
                logger.error(f"Randevu saatleri oluşturulurken hata: {str(e)}")
                randevu_saatleri = ['09:00', '10:00', '11:00', '13:00', '14:00', '15:00', '16:00', '17:00']
                
            return randevu_saatleri
    except Exception as e:
        logger.error(f"Randevu saatleri oluşturulurken hata: {str(e)}")
        return ['09:00', '10:00', '11:00', '13:00', '14:00', '15:00', '16:00', '17:00']

def set_online_whatsapp_sablon(sablon):
    try:
        with closing(get_db_connection()) as conn:
            try:
                # Önce sütunun var olup olmadığını kontrol et
                conn.execute("SELECT online_whatsapp_sablon FROM bildirim_ayarlar LIMIT 1")
                
                # Sütun varsa, devam et
                # Eğer hiç kayıt yoksa ekle, varsa güncelle
                row = conn.execute("SELECT id FROM bildirim_ayarlar ORDER BY id DESC LIMIT 1").fetchone()
                if row:
                    conn.execute("UPDATE bildirim_ayarlar SET online_whatsapp_sablon = ? WHERE id = ?", (sablon, row['id']))
                else:
                    conn.execute("INSERT INTO bildirim_ayarlar (online_whatsapp_sablon) VALUES (?)", (sablon,))
                conn.commit()
            except sqlite3.OperationalError:
                # Sütun yoksa, sütunu ekle ve tekrar dene
                conn.execute("ALTER TABLE bildirim_ayarlar ADD COLUMN online_whatsapp_sablon TEXT")
                conn.commit()
                # Tekrar dene
                set_online_whatsapp_sablon(sablon)
    except Exception as e:
        logger.error(f"Online WhatsApp şablonu kaydedilirken hata: {str(e)}")

def init_db():
    with app.app_context():
        with closing(get_db_connection()) as conn:
            # Bildirim Ayarları Tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS bildirim_ayarlar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    whatsapp_sablon TEXT,
                    online_whatsapp_sablon TEXT
                )
            ''')
            
            # Mevcut bildirim_ayarlar tablosuna online_whatsapp_sablon sütunu ekle (eğer yoksa)
            try:
                conn.execute("SELECT online_whatsapp_sablon FROM bildirim_ayarlar LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE bildirim_ayarlar ADD COLUMN online_whatsapp_sablon TEXT")
                conn.commit()

            # Kullanıcılar Tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS kullanicilar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kullanici_adi TEXT NOT NULL UNIQUE,
                    sifre_hash TEXT NOT NULL,
                    rol TEXT NOT NULL DEFAULT 'personel'
                )
            ''')            
            # Stoklar Tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS stoklar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    urun_adi TEXT NOT NULL,
                    stok_adeti INTEGER NOT NULL DEFAULT 0,
                    satin_alinan_firma TEXT,
                    birim_cinsi TEXT NOT NULL
                )
            ''')
            # Müşteriler Tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS müşteriler (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ad TEXT NOT NULL,
                    telefon TEXT NOT NULL UNIQUE,
                    adres TEXT,
                    bakiye REAL DEFAULT 0,
                    dogum_tarihi TEXT,
                    cinsiyet TEXT,
                    kayit_tarihi TEXT,
                    referans_id INTEGER,
                    notlar TEXT,
                    meslek TEXT,
                    FOREIGN KEY (referans_id) REFERENCES müşteriler(id)
    )
''')
            # Çalışanlar Tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS çalışanlar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ad TEXT NOT NULL
                )
            ''')
            # Hizmetler Tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS hizmetler (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hizmet_adi TEXT NOT NULL UNIQUE,
                    seans INTEGER NOT NULL DEFAULT 1,
                    fiyat REAL DEFAULT 0
                )
            ''')
            
            # Hizmet-Çalışan İlişki Tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS calisan_hizmet (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    calisan_id INTEGER NOT NULL,
                    hizmet_id INTEGER NOT NULL,
                    FOREIGN KEY (calisan_id) REFERENCES çalışanlar(id),
                    FOREIGN KEY (hizmet_id) REFERENCES hizmetler(id),
                    UNIQUE(calisan_id, hizmet_id)
                )
            ''')
            
            # Mevcut hizmetler tablosuna calisan_id ve fiyat sütunlarını ekle (eğer yoksa)
            try:
                conn.execute("SELECT calisan_id FROM hizmetler LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE hizmetler ADD COLUMN calisan_id INTEGER REFERENCES çalışanlar(id)")
                
            try:
                conn.execute("SELECT fiyat FROM hizmetler LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE hizmetler ADD COLUMN fiyat REAL DEFAULT 0")
                
            conn.commit()
            # Çalışan-Hizmet İlişki Tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS calisan_hizmet (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    calisan_id INTEGER NOT NULL,
                    hizmet_id INTEGER NOT NULL,
                    FOREIGN KEY (calisan_id) REFERENCES çalışanlar(id),
                    FOREIGN KEY (hizmet_id) REFERENCES hizmetler(id),
                    UNIQUE(calisan_id, hizmet_id)
                )
            ''')
            
            # Eski hizmet_calisan tablosundan verileri yeni calisan_hizmet tablosuna aktar
            try:
                # Önce eski tablonun var olup olmadığını kontrol et
                old_table_exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='hizmet_calisan'").fetchone()
                if old_table_exists:
                    # Eski tablodan verileri al
                    old_data = conn.execute("SELECT hizmet_id, calisan_id FROM hizmet_calisan").fetchall()
                    
                    # Yeni tabloya aktar
                    for row in old_data:
                        try:
                            conn.execute(
                                "INSERT OR IGNORE INTO calisan_hizmet (calisan_id, hizmet_id) VALUES (?, ?)",
                                (row['calisan_id'], row['hizmet_id'])
                            )
                        except Exception as e:
                            logger.error(f"Veri aktarım hatası: {str(e)}")
                    
                    # Eski tabloyu sil
                    conn.execute("DROP TABLE IF EXISTS hizmet_calisan")
                    logger.info("Eski hizmet_calisan tablosu silindi ve veriler aktarıldı.")
            except Exception as e:
                logger.error(f"Tablo kontrolü veya veri aktarımı sırasında hata: {str(e)}")
            # Randevular Tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS randevular (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    musteri_id INTEGER,
                    çalışan_id INTEGER,
                    tarih TEXT NOT NULL,
                    saat TEXT NOT NULL,
                    hizmet TEXT NOT NULL,
                    seans INTEGER NOT NULL DEFAULT 1,
                    durum TEXT DEFAULT 'Bekleniyor',
                    FOREIGN KEY (musteri_id) REFERENCES müşteriler (id),
                    FOREIGN KEY (çalışan_id) REFERENCES çalışanlar (id)
                )
            ''')
            # Tahsilatlar Tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS tahsilatlar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    musteri_id INTEGER NOT NULL,
                    tutar REAL NOT NULL,
                    tarih TEXT NOT NULL,
                    aciklama TEXT,
                    odeme_sekli TEXT,
                    taksit_id INTEGER,
                    satis_id INTEGER,
                    FOREIGN KEY (musteri_id) REFERENCES müşteriler (id)
                )
            ''')
            # Silinen Randevular Log Tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS silinen_randevular (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    randevu_id INTEGER NOT NULL,
                    sebep TEXT NOT NULL,
                    silinme_tarihi TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
             # satislar tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS satislar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    musteri_id INTEGER NOT NULL,
                    urun_id INTEGER NOT NULL,
                    fiyat REAL NOT NULL,
                    aciklama TEXT,
                    tarih TEXT NOT NULL,
                    toplam_seans INTEGER DEFAULT 1,
                    kalan_seans INTEGER DEFAULT 1,
                    odendi INTEGER DEFAULT 0,
                    FOREIGN KEY (musteri_id) REFERENCES müşteriler (id),
                    FOREIGN KEY (urun_id) REFERENCES stoklar (id)
                )
            ''')

            # Gelir / Gider Defteri Tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS gelir_gider (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tarih TEXT NOT NULL,
                    tur TEXT NOT NULL CHECK(tur IN ('Gelir', 'Gider')),
                    tutar REAL NOT NULL,
                    odeme_sekli TEXT,
                    aciklama TEXT,
                    musteri_id INTEGER
                )
            ''')
            # Taksitler Tablosu
            conn.execute('''
            CREATE TABLE IF NOT EXISTS taksitler (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                satis_id INTEGER NOT NULL,
                tutar REAL NOT NULL,
                tarih TEXT NOT NULL,
                son_odeme_tarihi TEXT,
                aciklama TEXT,
                odendi INTEGER DEFAULT 0,
                sira INTEGER,
                orijinal_taksit_id INTEGER,
                FOREIGN KEY (satis_id) REFERENCES satislar(id)
                )
            ''')
            
            # Sistem Ayarları Tablosu
            conn.execute('''
                CREATE TABLE IF NOT EXISTS sistem_ayarlar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    anahtar TEXT NOT NULL UNIQUE,
                    deger TEXT
                )
            ''')
            
            # Mevcut satislar tablosuna odendi sütunu ekle (eğer yoksa)
            try:
                conn.execute("SELECT odendi FROM satislar LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE satislar ADD COLUMN odendi INTEGER DEFAULT 0")
                
            # Mevcut taksitler tablosuna odendi sütunu ekle (eğer yoksa)
            try:
                conn.execute("SELECT odendi FROM taksitler LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE taksitler ADD COLUMN odendi INTEGER DEFAULT 0")
                
            # Mevcut taksitler tablosuna son_odeme_tarihi sütunu ekle (eğer yoksa)
            try:
                conn.execute("SELECT son_odeme_tarihi FROM taksitler LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE taksitler ADD COLUMN son_odeme_tarihi TEXT")
                
            # Mevcut taksitler tablosuna sira sütunu ekle (eğer yoksa)
            try:
                conn.execute("SELECT sira FROM taksitler LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE taksitler ADD COLUMN sira INTEGER")
                
            # Mevcut taksitler tablosuna orijinal_taksit_id sütunu ekle (eğer yoksa)
            try:
                conn.execute("SELECT orijinal_taksit_id FROM taksitler LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE taksitler ADD COLUMN orijinal_taksit_id INTEGER")
                
            conn.commit()

@app.route('/')
def index():
    from datetime import datetime
    
    bugun = datetime.now().strftime('%Y-%m-%d')
    
    with closing(get_db_connection()) as conn:
        # Bugünkü randevu sayısı
        bugun_randevu = conn.execute(
            "SELECT COUNT(*) FROM randevular WHERE tarih = ?", 
            (bugun,)
        ).fetchone()[0] or 0
        
        # Bekleyen randevu sayısı
        bekleyen_randevu = conn.execute(
            "SELECT COUNT(*) FROM randevular WHERE durum = 'Bekleniyor'"
        ).fetchone()[0] or 0
        
        # Günlük gelir
        gunluk_gelir = conn.execute(
            "SELECT SUM(tutar) FROM gelir_gider WHERE tur = 'Gelir' AND tarih LIKE ?", 
            (f"{bugun}%",)
        ).fetchone()[0] or 0
        
        # Toplam müşteri sayısı
        musteri_sayisi = conn.execute(
            "SELECT COUNT(*) FROM müşteriler"
        ).fetchone()[0] or 0
        
        # Güncel yıl
        current_year = datetime.now().year
    
    return render_template('index.html', 
                          bugun_randevu=bugun_randevu,
                          bekleyen_randevu=bekleyen_randevu,
                          gunluk_gelir=gunluk_gelir,
                          musteri_sayisi=musteri_sayisi,
                          current_year=current_year)


# Müşteri Yönetimi
@app.route('/musteri_kayit', methods=['GET', 'POST'])
@login_required
def musteri_kayit():
    with closing(get_db_connection()) as conn:
        mevcut_musteriler = conn.execute('SELECT id, ad FROM müşteriler ORDER BY ad').fetchall()
    if request.method == 'POST':
        ad = request.form.get('ad', '').strip().title()
        telefon = request.form.get('telefon', '').strip()
        adres = request.form.get('adres', '').strip()
        dogum_tarihi = request.form.get('dogum_tarihi', '')
        cinsiyet = request.form.get('cinsiyet', '')
        referans_id = request.form.get('referans_id', '')
        meslek = request.form.get('meslek', '').strip()
        notlar = request.form.get('notlar', '').strip()
        
        # Boş referans_id kontrolü
        if not referans_id:
            referans_id = None
            
        # Validasyon
        if not ad or not telefon:
            flash('Ad ve telefon alanları zorunludur!', 'danger')
            return render_template('musteri_kayit.html', mevcut_musteriler=mevcut_musteriler)
            
        try:
            with closing(get_db_connection()) as conn:
                # Telefon numarası kontrolü
                existing = conn.execute('SELECT id FROM müşteriler WHERE telefon = ?', (telefon,)).fetchone()
                if existing:
                    flash('Bu telefon numarası ile kayıtlı bir müşteri zaten var!', 'warning')
                    return render_template('musteri_kayit.html', mevcut_musteriler=mevcut_musteriler)
                
                # Yeni müşteri kaydı
                conn.execute('''
                    INSERT INTO müşteriler (ad, telefon, adres, dogum_tarihi, cinsiyet, kayit_tarihi, referans_id, meslek, notlar)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (ad, telefon, adres, dogum_tarihi, cinsiyet, datetime.now().strftime('%Y-%m-%d'), referans_id, meslek, notlar))
                conn.commit()
                
                flash('Müşteri başarıyla kaydedildi!', 'success')
                return redirect(url_for('musteri_listesi'))
        except Exception as e:
            flash(f'Bir hata oluştu: {str(e)}', 'danger')
            logger.error(f"Müşteri kaydı sırasında hata: {str(e)}")
            
    return render_template('musteri_kayit.html', mevcut_musteriler=mevcut_musteriler)


@app.template_filter('tarih_format')
def tarih_format(value):
    # örnek: 2024-05-29 14:30:00 -> 29.05.2024 14:30, 2024-05-29 -> 29.05.2024
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
    except Exception:
        try:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            return value

@app.route('/musteri_listesi')
def musteri_listesi():
    try:
        with closing(get_db_connection()) as conn:
            musteriler = conn.execute('''
                SELECT m.*, 
                       (SELECT COUNT(*) FROM taksitler t 
                        JOIN satislar s ON t.satis_id = s.id 
                        WHERE s.musteri_id = m.id AND t.odendi = 0) as odenmemis_taksit_sayisi,
                       (SELECT COUNT(*) FROM taksitler t 
                        JOIN satislar s ON t.satis_id = s.id 
                        WHERE s.musteri_id = m.id AND t.odendi = 0 
                        AND t.son_odeme_tarihi < date('now')) as geciken_taksit_sayisi,
                       (SELECT SUM(s.kalan_seans) FROM satislar s 
                        WHERE s.musteri_id = m.id) as toplam_kalan_seans
                FROM müşteriler m 
                ORDER BY m.ad
            ''').fetchall()
        return render_template('musteri_listesi.html', musteriler=musteriler)
    except Exception as e:
        logger.error("Müşteri listesi çekilirken hata: %s", e)
        flash(f'Veri alınamadı: {str(e)}', 'danger')
        return redirect(url_for('index'))
    
@app.route('/musteri_detay/<int:musteri_id>')
def musteri_detay(musteri_id):
    try:
        with closing(get_db_connection()) as conn:
            musteri = conn.execute('SELECT * FROM müşteriler WHERE id = ?', (musteri_id,)).fetchone()
            randevular = conn.execute(''' 
                SELECT randevular.id, randevular.tarih, randevular.saat, randevular.hizmet, randevular.seans, 
                       randevular.durum, randevular.durum_guncelleme_zamani, çalışanlar.ad AS calisan_ad
                FROM randevular
                LEFT JOIN çalışanlar ON randevular.çalışan_id = çalışanlar.id
                WHERE randevular.musteri_id = ?
            ''', (musteri_id,)).fetchall()
            tahsilatlar = conn.execute(''' 
                SELECT t.*, tk.son_odeme_tarihi
                FROM tahsilatlar t
                LEFT JOIN taksitler tk ON t.taksit_id = tk.id
                WHERE t.musteri_id = ?
                ORDER BY t.tarih DESC
            ''', (musteri_id,)).fetchall()
            stoklar = conn.execute('SELECT * FROM stoklar ORDER BY urun_adi').fetchall()
            satislar = conn.execute('''
    SELECT satislar.id, satislar.tarih, hizmetler.hizmet_adi, satislar.miktar, satislar.fiyat, satislar.aciklama,
           satislar.toplam_seans, satislar.kalan_seans, satislar.odendi
    FROM satislar
    LEFT JOIN hizmetler ON satislar.urun_id = hizmetler.id
    WHERE satislar.musteri_id = ?
    ORDER BY satislar.tarih DESC
''', (musteri_id,)).fetchall()


            mevcut_musteriler = conn.execute('SELECT id, ad FROM müşteriler').fetchall()
            hizmetler = conn.execute('SELECT * FROM hizmetler').fetchall()
            satislar_hizmet = conn.execute('SELECT musteri_id, urun_id FROM satislar').fetchall()
            # TAKSİTLERİ ÇEK
            taksitler = conn.execute('''
    SELECT t.*, t.orijinal_taksit_id, s.tarih as satis_tarihi, h.hizmet_adi
    FROM taksitler t
    LEFT JOIN satislar s ON t.satis_id = s.id
    LEFT JOIN hizmetler h ON s.urun_id = h.id
    WHERE s.musteri_id = ?
    ORDER BY t.id ASC
''', (musteri_id,)).fetchall()
            # Hangi taksitlerin ödendiğini bul
            odendi_taksitler = set()
            for row in tahsilatlar:
                if row['taksit_id']:
                    odendi_taksitler.add(row['taksit_id'])
            taksitler_list = []
            for t in taksitler:
                t = dict(t)
                t['odendi'] = t['id'] in odendi_taksitler
                taksitler_list.append(t)
    except Exception as e:
        logger.error("Müşteri detayları çekilirken hata: %s", e)
        flash(f'Veri alınamadı: {str(e)}', 'danger')
        return redirect(url_for('musteri_listesi'))

    if not musteri:
        flash('Müşteri bulunamadı!', 'danger')
        return redirect(url_for('musteri_listesi'))

    # Bugünün tarihini al (son ödeme tarihi geçmiş taksitleri vurgulamak için)
    now_date = datetime.now().strftime('%Y-%m-%d')
    
    # Bugünün tarihini al
    bugun = datetime.now().date()
    
    # Taksitlerin son_odeme_tarihi'ni kontrol et ve gecikme durumunu belirle
    for i in range(len(taksitler_list)):
        try:
            # SQLite row nesnesini dict'e çevir
            taksit = dict(taksitler_list[i])
            
            # Varsayılan olarak gecikti değerini False yap
            taksit['gecikti'] = False
            
            # Son ödeme tarihi ve ödendi durumunu kontrol et
            if taksit.get('son_odeme_tarihi') and not taksit.get('odendi', False):
                try:
                    # Son ödeme tarihini datetime nesnesine çevir
                    son_odeme = datetime.strptime(taksit['son_odeme_tarihi'], '%Y-%m-%d').date()
                    # Gecikme durumunu belirle
                    if son_odeme < bugun:
                        taksit['gecikti'] = True
                except (ValueError, TypeError):
                    # Tarih dönüştürme hatası
                    pass
            
            # Dict'i listeye geri koy
            taksitler_list[i] = taksit
        except Exception as e:
            logger.error(f"Taksit gecikme durumu hesaplanırken hata: {str(e)}")
    
    return render_template(
        'musteri_detay.html',
        musteri=musteri,
        randevular=randevular,
        tahsilatlar=tahsilatlar,
        stoklar=stoklar,
        satislar=satislar,
        mevcut_musteriler=mevcut_musteriler,
        hizmetler=hizmetler,
        satislar_hizmet=satislar_hizmet,
        taksitler=taksitler_list,
        now_date=now_date
    )

@app.route('/musteri_duzenle/<int:musteri_id>', methods=['GET', 'POST'])
@login_required
def musteri_duzenle(musteri_id):
    try:
        with closing(get_db_connection()) as conn:
            musteri = conn.execute('SELECT * FROM müşteriler WHERE id = ?', (musteri_id,)).fetchone()
    except Exception as e:
        logger.error("Müşteri bilgileri çekilirken hata: %s", e)
        flash('Müşteri bulunamadı!', 'danger')
        return redirect(url_for('musteri_listesi'))

    if not musteri:
        flash('Müşteri bulunamadı!', 'danger')
        return redirect(url_for('musteri_listesi'))

    if request.method == 'POST':
        ad = request.form.get('ad', '').strip().title()
        telefon = request.form.get('telefon', '').strip()
        adres = request.form.get('adres', '').strip().lower()
        try:
            with closing(get_db_connection()) as conn:
                with conn:
                    conn.execute(
                        'UPDATE müşteriler SET ad = ?, telefon = ?, adres = ? WHERE id = ?',
                        (ad, telefon, adres, musteri_id)
                    )
            flash('Müşteri bilgileri başarıyla güncellendi.', 'success')
            return redirect(url_for('musteri_listesi'))
        except sqlite3.IntegrityError:
            flash('Bu telefon numarası zaten kayıtlı!', 'danger')
        except Exception as e:
            logger.error("Müşteri güncelleme hatası: %s", e)
            flash(f'Hata oluştu: {str(e)}', 'danger')
    return render_template('musteri_duzenle.html', musteri=musteri)

@app.route('/musteri_sil/<int:musteri_id>', methods=['POST'])
@login_required
def musteri_sil(musteri_id):
    try:
        with closing(get_db_connection()) as conn:
            with conn:
                conn.execute('DELETE FROM müşteriler WHERE id = ?', (musteri_id,))
        flash("Müşteri başarıyla silindi.", "success")
    except Exception as e:
        logger.error("Müşteri silme hatası: %s", e)
        flash(f"Müşteri silinirken hata oluştu: {str(e)}", "danger")
    return redirect(url_for('musteri_listesi'))


# Hizmet Yönetimi
@app.route('/api/hizmet_bilgisi/<hizmet_adi>')
def hizmet_bilgisi(hizmet_adi):
    try:
        with closing(get_db_connection()) as conn:
            hizmet = conn.execute("SELECT seans FROM hizmetler WHERE hizmet_adi = ?", (hizmet_adi,)).fetchone()
        if hizmet:
            return jsonify({'seans': hizmet['seans']})
        else:
            return jsonify({'seans': 1})
    except Exception as e:
        app.logger.error(f"API hatası: {e}")
        return jsonify({'seans': 1}), 500

@app.route('/api/hizmet_calisanlar')
def hizmet_calisanlar():
    hizmet_id = request.args.get('hizmet_id')
    if not hizmet_id:
        return jsonify({'error': 'Hizmet ID parametresi gerekli'}), 400
        
    try:
        with closing(get_db_connection()) as conn:
            # Önce hizmetin calisan_id değerini kontrol et
            hizmet = conn.execute('SELECT calisan_id FROM hizmetler WHERE id = ?', (hizmet_id,)).fetchone()
            
            # Eğer calisan_id varsa ve virgül içeriyorsa, ilişkisel tabloya bakmadan direkt çalışanları getir
            if hizmet and hizmet['calisan_id'] and ',' in str(hizmet['calisan_id']):
                calisan_ids = [cid.strip() for cid in str(hizmet['calisan_id']).split(',') if cid.strip()]
                if calisan_ids:
                    placeholders = ','.join(['?' for _ in calisan_ids])
                    calisanlar = conn.execute(f'SELECT id, ad FROM çalışanlar WHERE id IN ({placeholders}) ORDER BY ad', calisan_ids).fetchall()
                    result = {
                        'calisanlar': [{'id': c['id'], 'ad': c['ad']} for c in calisanlar]
                    }
                    return jsonify(result)
            
            # Belirli bir hizmeti verebilen çalışanları getir (ilişkisel tablodan)
            calisanlar = conn.execute('''
                SELECT c.id, c.ad
                FROM çalışanlar c
                JOIN calisan_hizmet ch ON c.id = ch.calisan_id
                WHERE ch.hizmet_id = ?
                ORDER BY c.ad
            ''', (hizmet_id,)).fetchall()
            
            # Sonuçları JSON formatına dönüştür
            result = {
                'calisanlar': [{'id': c['id'], 'ad': c['ad']} for c in calisanlar]
            }
            
            return jsonify(result)
    except Exception as e:
        logger.error(f"Hizmet çalışanları API hatası: {str(e)}")
        return jsonify({'error': str(e)}), 500

from flask import request, redirect, url_for, flash
from contextlib import closing

@app.route('/hizmet_ekle', methods=['POST'])
@login_required
def hizmet_ekle():
    if current_user.rol != 'admin':
        flash('Yetkiniz yok!', 'danger')
        return redirect(url_for('hizmet_tanimlari'))
    hizmet_adi = request.form.get('hizmet_adi', '').strip()
    seans = request.form.get('seans', 1, type=int)
    fiyat = request.form.get('fiyat', 0, type=float)
    calisan_ids = request.form.getlist('calisan_ids')
    
    if not hizmet_adi or seans < 1:
        flash('Hizmet adı ve seans zorunludur.', 'danger')
        return redirect(url_for('hizmet_tanimlari'))
    try:
        with closing(get_db_connection()) as conn:
            with conn:
                # Önce hizmeti ekle
                # Çalışan ID'lerini virgülle ayrılmış string olarak birleştir
                calisan_id_str = ','.join(calisan_ids) if calisan_ids else ''
                
                conn.execute(
                    "INSERT INTO hizmetler (hizmet_adi, seans, fiyat, calisan_id) VALUES (?, ?, ?, ?)",
                    (hizmet_adi, seans, fiyat, calisan_id_str)
                )
                
                # Eklenen hizmetin ID'sini al
                hizmet_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                
                # Çalışan ilişkileri artık calisan_id sütununda virgülle ayrılmış olarak tutuluyor
        flash('Hizmet eklendi.', 'success')
    except Exception as e:
        flash(f'Hata: {str(e)}', 'danger')
    return redirect(url_for('hizmet_tanimlari'))

@app.route('/hizmet_guncelle/<int:id>', methods=['POST'])
@login_required
def hizmet_guncelle(id):
    if current_user.rol != 'admin':
        flash('Yetkiniz yok!', 'danger')
        return redirect(url_for('hizmet_tanimlari'))
    hizmet_adi = request.form.get('hizmet_adi', '').strip()
    seans = request.form.get('seans', 1, type=int)
    fiyat = request.form.get('fiyat', 0, type=float)
    calisan_ids = request.form.getlist('calisan_ids')
    
    if not hizmet_adi or seans < 1:
        flash('Hizmet adı ve seans zorunludur.', 'danger')
        return redirect(url_for('hizmet_tanimlari'))
    try:
        with closing(get_db_connection()) as conn:
            with conn:
                # Önce hizmeti güncelle
                # Çalışan ID'lerini virgülle ayrılmış string olarak birleştir
                calisan_id_str = ','.join(calisan_ids) if calisan_ids else ''
                
                conn.execute(
                    "UPDATE hizmetler SET hizmet_adi = ?, seans = ?, fiyat = ?, calisan_id = ? WHERE id = ?",
                    (hizmet_adi, seans, fiyat, calisan_id_str, id)
                )
                
                # Çalışan ilişkileri artık calisan_id sütununda virgülle ayrılmış olarak tutuluyor
        flash('Hizmet güncellendi.', 'success')
    except Exception as e:
        flash(f'Hata: {str(e)}', 'danger')
    return redirect(url_for('hizmet_tanimlari'))

@app.route('/hizmet_sil/<int:id>', methods=['POST'])
@login_required
def hizmet_sil(id):
    if current_user.rol != 'admin':
        flash('Yetkiniz yok!', 'danger')
        return redirect(url_for('hizmet_tanimlari'))
    try:
        with closing(get_db_connection()) as conn:
            with conn:
                conn.execute("DELETE FROM hizmetler WHERE id = ?", (id,))
        flash('Hizmet silindi.', 'success')
    except Exception as e:
        flash(f'Hata: {str(e)}', 'danger')
    return redirect(url_for('hizmet_tanimlari'))

# Randevu Yönetimi
@app.route('/api/randevular')
def api_randevular():
    selected_date = request.args.get('date')
    selected_month = request.args.get('month')
    try:
        with closing(get_db_connection()) as conn:
            query = '''
                SELECT randevular.id, randevular.saat, müşteriler.ad, hizmet, durum,
                       randevular.tarih || 'T' || randevular.saat AS start
                FROM randevular
                INNER JOIN müşteriler ON randevular.musteri_id = müşteriler.id
                WHERE randevular.durum != 'Geldi'
            '''
            params = []
            if selected_date:
                query += ' AND tarih = ?'
                params.append(selected_date)
            elif selected_month:
                query += ' AND strftime("mY-%mY", tarih) = ?'
                params.append(selected_month)
                
            randevular = conn.execute(query, params).fetchall()
            
            # Randevu durumlarına göre renk ekleme
            events = []
            for r in randevular:
                event = dict(r)
                # Başlığı saat + isim + hizmet olarak ayarla
                event['title'] = f"{r['ad']} – {r['hizmet']}"
                # Tooltip için detaylı açıklama
                event['description'] = f"Müşteri: {r['ad']}\nSaat: {r['saat']}\nHizmet: {r['hizmet']}\nDurum: {r['durum']}"
                # Tooltip özelliğini aktifleştir
                event['extendedProps'] = {
                    'tooltip': f"Müşteri: {r['ad']}<br>Saat: {r['saat']}<br>Hizmet: {r['hizmet']}<br>Durum: {r['durum']}"
                }
                
                # Durum renklerini ayarla
                if r['durum'] == 'Onaylandı':
                    event['color'] = '#28a745'  # Yeşil
                elif r['durum'] == 'Bekliyor' or r['durum'] == 'Bekleniyor':
                    event['color'] = '#fd7e14'  # Turuncu
                elif r['durum'] == 'İptal':
                    event['color'] = '#dc3545'  # Kırmızı
                events.append(event)
                
        return jsonify(events)
    except Exception as e:
        logger.error("API randevular hatası: %s", e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

        
@app.route('/api/yaklasan_randevular')
def yaklasan_randevular():
    try:
        # Şu anki tarih ve saat
        simdi = datetime.now()
        bugun = simdi.strftime('%Y-%m-%d')
        
        # 30 dakika içindeki randevuları getir
        otuz_dk_sonra = (simdi + timedelta(minutes=30)).strftime('%H:%M')
        
        with closing(get_db_connection()) as conn:
            yaklasan = conn.execute('''
                SELECT randevular.id, müşteriler.ad, randevular.tarih, randevular.saat, 
                       randevular.hizmet, randevular.durum
                FROM randevular
                INNER JOIN müşteriler ON randevular.musteri_id = müşteriler.id
                WHERE randevular.tarih = ? AND randevular.saat <= ? AND randevular.saat >= ? 
                AND randevular.durum IN ('Bekleniyor', 'Onaylandı')
                ORDER BY randevular.saat
            ''', (bugun, otuz_dk_sonra, simdi.strftime('%H:%M'))).fetchall()
            
            result = [{
                'id': r['id'],
                'musteri': r['ad'],
                'tarih': r['tarih'],
                'saat': r['saat'],
                'hizmet': r['hizmet'],
                'durum': r['durum']
            } for r in yaklasan]
            
            return jsonify(result)
    except Exception as e:
        logger.error("Yaklaşan randevular API hatası: %s", e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/hizmet_satis/<int:musteri_id>', methods=['GET', 'POST'])
def hizmet_satis(musteri_id):
    with closing(get_db_connection()) as conn:
        hizmetler = conn.execute('SELECT * FROM hizmetler').fetchall()
        musteri = conn.execute('SELECT * FROM müşteriler WHERE id = ?', (musteri_id,)).fetchone()
    if request.method == 'POST':
        hizmet_id = request.form.get('hizmet_id')
        seans = int(request.form.get('seans', 1))
        toplam_ucret = float(request.form.get('toplam_ucret', 0))
        taksit_var = request.form.get('taksit_var') == 'on'
        taksit_tutarlar = request.form.getlist('taksit_tutar')
        aciklama = request.form.get('aciklama', '').strip()

        if not hizmet_id or seans < 1 or toplam_ucret <= 0:
            flash("Tüm alanları doldurun!", "danger")
            return redirect(url_for('hizmet_satis', musteri_id=musteri_id))

        try:
            with closing(get_db_connection()) as conn:
                with conn:
                    hizmet = conn.execute('SELECT hizmet_adi FROM hizmetler WHERE id = ?', (hizmet_id,)).fetchone()
                    conn.execute(
                        '''INSERT INTO satislar (musteri_id, urun_id, miktar, fiyat, aciklama, tarih, toplam_seans, kalan_seans)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                        (musteri_id, hizmet_id, 1, toplam_ucret, aciklama, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), seans, seans)
                    )

                    if taksit_var and taksit_tutarlar:
                        satis_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
                        satis_tarihi = datetime.now()

                        # Peşinat (ilk taksit)
                        pesinat_tutar = float(taksit_tutarlar[0])
                        conn.execute(
                            '''INSERT INTO taksitler (satis_id, tutar, tarih, son_odeme_tarihi, aciklama, sira)
                               VALUES (?, ?, ?, ?, ?, ?)''',
                            (satis_id, pesinat_tutar, satis_tarihi.strftime('%Y-%m-%d'), satis_tarihi.strftime('%Y-%m-%d'), 'Peşinat', 0)
                        )

                        # Kalan taksitler, peşinat hariç
                        for i, tutar in enumerate(taksit_tutarlar[1:], start=1):
                            tutar = float(tutar)
                            son_odeme_tarihi = (satis_tarihi + timedelta(days=30 * i)).strftime('%Y-%m-%d')
                            taksit_sira = i  # 1'den başlıyor, 1. taksit gibi
                            conn.execute(
                                '''INSERT INTO taksitler (satis_id, tutar, tarih, son_odeme_tarihi, aciklama, sira)
                                   VALUES (?, ?, ?, ?, ?, ?)''',
                                (satis_id, tutar, satis_tarihi.strftime('%Y-%m-%d'), son_odeme_tarihi, f"{taksit_sira}. Taksit", taksit_sira)
                            )

                    # Müşteri bakiyesini güncelle
                    conn.execute('UPDATE müşteriler SET bakiye = bakiye - ? WHERE id = ?', (toplam_ucret, musteri_id))
                conn.commit()
            flash("Satış başarıyla kaydedildi!", "success")
            return redirect(url_for('musteri_detay', musteri_id=musteri_id))
        except Exception as e:
            logger.error("Satış kaydı hatası: %s", e)
            flash(f"Hata oluştu: {str(e)}", "danger")
            return redirect(url_for('hizmet_satis', musteri_id=musteri_id))

    return render_template('hizmet_satis.html', hizmetler=hizmetler, musteri=musteri)



@app.route('/randevu_al', methods=['GET', 'POST'])
@login_required
def randevu_al():
    try:
        with closing(get_db_connection()) as conn:
            tum_musteriler = conn.execute('SELECT * FROM müşteriler ORDER BY ad').fetchall()
            # Hizmetleri getir
            hizmetler = [dict(row) for row in conn.execute('SELECT * FROM hizmetler').fetchall()]
            
            # Çalışanları getir
            calisanlar = conn.execute('SELECT * FROM çalışanlar').fetchall()
            
            # Randevu saatlerini otomatik oluştur
            randevu_saatleri = generate_randevu_saatleri()
            
            # Hizmetlere çalışan adlarını ekle
            for i in range(len(hizmetler)):
                h = hizmetler[i]
                if 'calisan_id' in h and h['calisan_id']:
                    for c in calisanlar:
                        if c['id'] == h['calisan_id']:
                            h['calisan_adi'] = c['ad']
                            break
                    if 'calisan_adi' not in h:
                        h['calisan_adi'] = None
                else:
                    h['calisan_adi'] = None
            satislar = conn.execute('SELECT musteri_id, urun_id FROM satislar').fetchall()
    except Exception as e:
        logger.error("Veri çekme hatası: %s", e)
        flash(f'Hata: {str(e)}', 'danger')
        tum_musteriler = []
        hizmetler = []
        satislar = []
    musteri_id = request.args.get('musteri_id')
    musteri = None
    satilmis_hizmet_idler = []
    kalan_seanslar = {}
    if musteri_id:
        try:
            with closing(get_db_connection()) as conn:
                musteri = conn.execute('SELECT * FROM müşteriler WHERE id = ?', (musteri_id,)).fetchone()
                satilmis_hizmet_idler = [
                    row['urun_id'] for row in conn.execute(
                        'SELECT urun_id FROM satislar WHERE musteri_id = ?', (musteri_id,)
                    ).fetchall()
                ]
                # Her hizmet için kalan seansları hesapla
                rows = conn.execute(
                    '''SELECT urun_id, SUM(kalan_seans) as kalan FROM satislar WHERE musteri_id = ? GROUP BY urun_id''',
                    (musteri_id,)
                ).fetchall()
                for row in rows:
                    kalan_seanslar[str(row['urun_id'])] = row['kalan']
        except Exception as e:
            logger.error("Müşteri bilgisi alınırken hata: %s", e)
            flash(f'Hata: {str(e)}', 'danger')
    if request.method == 'POST':
        ad = request.form.get('ad', '').strip().title()
        telefon = request.form.get('telefon', '').strip()
        adres = request.form.get('adres', '').strip().lower()
        tarih = request.form.get('tarih', '')
        saat = request.form.get('saat', '')
        hizmet = request.form.get('hizmet', '')
        calisan_id = request.form.get('calisan_id')
        seans = request.form.get('seans', 1, type=int)
        ucret = request.form.get('ucret')
        
        # Pazar günü kontrolü
        try:
            tarih_obj = datetime.strptime(tarih, '%Y-%m-%d')
            if tarih_obj.weekday() == 6:  # 6 = Pazar
                flash("Pazar günleri salon kapalıdır, randevu oluşturulamaz!", "danger")
                return render_template('randevu_al.html',
                                    hizmetler=hizmetler,
                                    musteri=musteri,
                                    musteri_id=musteri_id,
                                    tum_musteriler=tum_musteriler,
                                    satilmis_hizmet_idler=satilmis_hizmet_idler,
                                    satislar=satislar,
                                    kalan_seanslar=kalan_seanslar)
        except ValueError:
            flash("Geçersiz tarih formatı!", "danger")
            return render_template('randevu_al.html',
                                hizmetler=hizmetler,
                                musteri=musteri,
                                musteri_id=musteri_id,
                                tum_musteriler=tum_musteriler,
                                satilmis_hizmet_idler=satilmis_hizmet_idler,
                                satislar=satislar,
                                kalan_seanslar=kalan_seanslar)
        
        # Hizmet id'sini bul
        hizmet_id = None
        for h in hizmetler:
            if h['hizmet_adi'] == hizmet:
                hizmet_id = str(h['id'])
                # Eğer calisan_id boşsa, hizmetin sorumlu çalışanını kullan
                if not calisan_id and h['calisan_id']:
                    calisan_id = h['calisan_id']
                break
                
        # Kalan seans kontrolü - sadece müşteri seçiliyse ve hizmet satın alınmışsa kontrol et
        if musteri_id and hizmet_id and hizmet_id in kalan_seanslar:
            # Kalan seans 0 veya daha az ise hata ver
            if kalan_seanslar[hizmet_id] <= 0:
                flash(f"Bu hizmet için kalan seans bulunmamaktadır!", "danger")
                return render_template('randevu_al.html',
                                    hizmetler=hizmetler,
                                    musteri=musteri,
                                    musteri_id=musteri_id,
                                    tum_musteriler=tum_musteriler,
                                    satilmis_hizmet_idler=satilmis_hizmet_idler,
                                    satislar=satislar,
                                    kalan_seanslar=kalan_seanslar)
            # Seans sayısı kalan seanstan fazla ise hata ver
            if seans > kalan_seanslar[hizmet_id]:
                flash(f"Bu hizmet için en fazla {kalan_seanslar[hizmet_id]} seans randevu verebilirsiniz!", "danger")
                return render_template('randevu_al.html',
                                    hizmetler=hizmetler,
                                    musteri=musteri,
                                    musteri_id=musteri_id,
                                    tum_musteriler=tum_musteriler,
                                    satilmis_hizmet_idler=satilmis_hizmet_idler,
                                    satislar=satislar,
                                    kalan_seanslar=kalan_seanslar)
        if not (ad and telefon and tarih and saat and hizmet):
            flash("Lütfen tüm zorunlu alanları doldurun!", "danger")
            return render_template('randevu_al.html',
                                   hizmetler=hizmetler,
                                   musteri=musteri,
                                   musteri_id=musteri_id,
                                   tum_musteriler=tum_musteriler,
                                   satilmis_hizmet_idler=satilmis_hizmet_idler,
                                   satislar=satislar,
                                   kalan_seanslar=kalan_seanslar)
        try:
            with closing(get_db_connection()) as conn:
                with conn:
                    # Seçilen çalışanın bu saatte başka randevusu var mı kontrol et
                    if calisan_id:
                        var_mi = conn.execute(
                            'SELECT id FROM randevular WHERE tarih = ? AND saat = ? AND çalışan_id = ?',
                            (tarih, saat, calisan_id)
                        ).fetchone()
                        
                        if var_mi:
                            flash("Seçilen hizmetin sorumlusu bu tarih ve saatte dolu!", "danger")
                            return render_template('randevu_al.html',
                                                hizmetler=hizmetler,
                                                musteri=musteri,
                                                musteri_id=musteri_id,
                                                tum_musteriler=tum_musteriler,
                                                satilmis_hizmet_idler=satilmis_hizmet_idler,
                                                satislar=satislar,
                                                kalan_seanslar=kalan_seanslar)
                    
                    # Çalışan adını al
                    calisan_adi = ''
                    if calisan_id:
                        calisan_row = conn.execute('SELECT ad FROM çalışanlar WHERE id = ?', (calisan_id,)).fetchone()
                        calisan_adi = calisan_row['ad'] if calisan_row else ''
                    
                    # Hizmet adını güncelle
                    hizmet_detayli = hizmet
                    if calisan_adi:
                        hizmet_detayli = f"{hizmet} - {calisan_adi}"
                    
                    musteri_check = conn.execute('SELECT id FROM müşteriler WHERE telefon = ?', (telefon,)).fetchone()
                    if musteri_check:
                        musteri_id = musteri_check['id']
                    else:
                        cursor = conn.execute(
                            'INSERT INTO müşteriler (ad, telefon, adres) VALUES (?, ?, ?)',
                            (ad, telefon, adres)
                        )
                        musteri_id = cursor.lastrowid
                    conn.execute(
                        'INSERT INTO randevular (musteri_id, çalışan_id, tarih, saat, hizmet, seans, durum) VALUES (?, ?, ?, ?, ?, ?, ?)',
                        (musteri_id, calisan_id, tarih, saat, hizmet_detayli, seans, 'Bekleniyor')
                    )
                    if ucret:
                        ucret = float(ucret)
                        conn.execute(
                            'UPDATE müşteriler SET bakiye = bakiye - ? WHERE id = ?',
                            (ucret, musteri_id)
                        )
                conn.commit()
            flash('Randevu başarıyla oluşturuldu!', 'success')
            return redirect(url_for('randevular'))
        except sqlite3.IntegrityError:
            flash('Bu telefon numarası zaten kayıtlı!', 'danger')
        except Exception as e:
            logger.error("Randevu oluşturma hatası: %s", e)
            flash(f'Bir hata oluştu: {str(e)}', 'danger')
    return render_template('randevu_al.html',
                           hizmetler=hizmetler,
                           musteri=musteri,
                           musteri_id=musteri_id,
                           tum_musteriler=tum_musteriler,
                           satilmis_hizmet_idler=satilmis_hizmet_idler,
                           satislar=satislar,
                           kalan_seanslar=kalan_seanslar,
                           randevu_saatleri=randevu_saatleri)
                           
# Çalışan Yönetimi Route'ları
@app.route('/calisan_ekle', methods=['POST'])
@login_required
def calisan_ekle():
    if current_user.rol != 'admin':
        flash('Yetkiniz yok!', 'danger')
        return redirect(url_for('calisan_yonetimi'))
    
    ad = request.form.get('ad', '').strip()
    if not ad:
        flash('Çalışan adı zorunludur!', 'danger')
        return redirect(url_for('calisan_yonetimi'))
    
    try:
        with closing(get_db_connection()) as conn:
            conn.execute('INSERT INTO çalışanlar (ad) VALUES (?)', (ad,))
            conn.commit()
        flash('Çalışan başarıyla eklendi!', 'success')
    except Exception as e:
        logger.error(f"Çalışan ekleme hatası: {str(e)}")
        flash(f'Hata oluştu: {str(e)}', 'danger')
    
    return redirect(url_for('calisan_yonetimi'))

@app.route('/calisan_duzenle/<int:id>', methods=['POST'])
@login_required
def calisan_duzenle(id):
    if current_user.rol != 'admin':
        flash('Yetkiniz yok!', 'danger')
        return redirect(url_for('calisan_yonetimi'))
    
    ad = request.form.get('ad', '').strip()
    if not ad:
        flash('Çalışan adı zorunludur!', 'danger')
        return redirect(url_for('calisan_yonetimi'))
    
    try:
        with closing(get_db_connection()) as conn:
            conn.execute('UPDATE çalışanlar SET ad = ? WHERE id = ?', (ad, id))
            conn.commit()
        flash('Çalışan başarıyla güncellendi!', 'success')
    except Exception as e:
        logger.error(f"Çalışan güncelleme hatası: {str(e)}")
        flash(f'Hata oluştu: {str(e)}', 'danger')
    
    return redirect(url_for('calisan_yonetimi'))

@app.route('/calisan_sil/<int:id>', methods=['POST'])
@login_required
def calisan_sil(id):
    if current_user.rol != 'admin':
        flash('Yetkiniz yok!', 'danger')
        return redirect(url_for('calisan_yonetimi'))
    
    try:
        with closing(get_db_connection()) as conn:
            conn.execute('DELETE FROM çalışanlar WHERE id = ?', (id,))
            conn.commit()
        flash('Çalışan başarıyla silindi!', 'success')
    except Exception as e:
        logger.error(f"Çalışan silme hatası: {str(e)}")
        flash(f'Hata oluştu: {str(e)}', 'danger')
    
    return redirect(url_for('calisan_yonetimi'))

@app.route('/whatsapp_gonder/<int:musteri_id>')
def whatsapp_gonder_route(musteri_id):
    try:
        with closing(get_db_connection()) as conn:
            musteri = conn.execute('SELECT * FROM müşteriler WHERE id = ?', (musteri_id,)).fetchone()
            randevu = conn.execute('SELECT tarih, saat, hizmet, seans FROM randevular WHERE musteri_id = ? ORDER BY tarih DESC, saat DESC LIMIT 1', (musteri_id,)).fetchone()
        if not musteri or not randevu:
            flash("Müşteri veya randevu bulunamadı!", "danger")
            return redirect(url_for('musteri_listesi'))
        telefon = "90" + musteri['telefon'].lstrip('0')
        sablon = get_whatsapp_sablon() or "Sayın {ad}, {tarih} tarihinde saat {saat}'de {hizmet} randevunuz bulunmaktadır."
        try:
            tarih_tr = datetime.strptime(randevu['tarih'], "%Y-%m-%d").strftime("%d/%m/%Y")
        except Exception:
            tarih_tr = randevu['tarih']
        
        mesaj = sablon.format(
            ad=randevu['ad'],
            tarih=tarih_tr,
            saat=randevu['saat'],
            hizmet=randevu['hizmet'],
            seans=randevu['seans'] if 'seans' in randevu.keys() else '',
            adres=randevu['adres'] if 'adres' in randevu.keys() else '',
            calisan='',
            firma='',
            telefon=randevu['telefon']
            )
        threading.Thread(target=whatsapp_mesaj_gonder, args=(telefon, mesaj)).start()
        flash("WhatsApp mesajı gönderiliyor (tarayıcıda kontrol edin).", "success")
    except Exception as e:
        flash(f"Mesaj gönderilemedi: {str(e)}", "danger")
    return redirect(url_for('musteri_listesi'))

@app.route('/whatsapp_gonder_randevu/<int:randevu_id>')
def whatsapp_gonder_randevu(randevu_id):
    try:
        with closing(get_db_connection()) as conn:
            randevu = conn.execute('''
                SELECT randevular.*, müşteriler.ad, müşteriler.telefon, müşteriler.adres
                FROM randevular
                INNER JOIN müşteriler ON randevular.musteri_id = müşteriler.id
                WHERE randevular.id = ?
            ''', (randevu_id,)).fetchone()
        if not randevu:
            flash("Randevu bulunamadı!", "danger")
            return redirect(url_for('randevular'))
        telefon = "90" + randevu['telefon'].lstrip('0')
        
        # Şablonu veritabanından çek
        sablon = get_whatsapp_sablon()

        try:
            tarih_tr = datetime.strptime(randevu['tarih'], "%Y-%m-%d").strftime("%d/%m/%Y")
        except Exception:
            tarih_tr = randevu['tarih']

        mesaj = sablon.format(
            ad=randevu['ad'],
            tarih=tarih_tr,
            saat=randevu['saat'],
            hizmet=randevu['hizmet'],
            seans=randevu['seans'] if 'seans' in randevu.keys() else '',
            adres=randevu['adres'] if 'adres' in randevu.keys() else '',
            calisan='',
            firma='',
            telefon=randevu['telefon']
        )
        threading.Thread(target=whatsapp_mesaj_gonder, args=(telefon, mesaj)).start()
        flash(f"WhatsApp mesajı {telefon} numarasına gönderiliyor.", "success")
    except Exception as e:
        logger.error(f"WhatsApp gönderim hatası: {e}")
        flash(f"Mesaj gönderilemedi: {str(e)}", "danger")
    return redirect(url_for('randevular'))



@app.route('/randevular')
def randevular():
    # Bugünün tarihini al
    bugun = datetime.now().strftime('%Y-%m-%d')
    
    try:
        with closing(get_db_connection()) as conn:
            # Tüm randevuları getir
            randevular = conn.execute('''
                SELECT r.*, m.ad 
                FROM randevular r
                INNER JOIN müşteriler m ON r.musteri_id = m.id
                ORDER BY r.tarih DESC, r.saat DESC
            ''').fetchall()
            
            # Bugün bekleyen randevular
            bugun_bekleyen = conn.execute('''
                SELECT COUNT(*) as sayi
                FROM randevular
                WHERE tarih = ? AND durum = 'Bekleniyor'
            ''', (bugun,)).fetchone()['sayi']
            
            # Bugün gelen randevular
            bugun_gelen = conn.execute('''
                SELECT COUNT(*) as sayi
                FROM randevular
                WHERE tarih = ? AND durum = 'Geldi'
            ''', (bugun,)).fetchone()['sayi']
            
            # Bugünkü hizmetler ve adetleri
            bugun_hizmetler = conn.execute('''
                SELECT hizmet as ad, COUNT(*) as adet
                FROM randevular
                WHERE tarih = ? AND durum = 'Bekleniyor'
                GROUP BY hizmet
            ''', (bugun,)).fetchall()
            
            # Çalışanları getir
            calisanlar = conn.execute('SELECT * FROM çalışanlar').fetchall()
    except Exception as e:
        logger.error("Randevular sayfası yüklenirken hata: %s", e)
        flash(f"Bir hata oluştu: {str(e)}", "danger")
        return redirect(url_for('index'))
    
    return render_template('randevular.html', 
                          randevular=randevular,
                          calisanlar=calisanlar,
                          bugun_bekleyen=bugun_bekleyen,
                          bugun_gelen=bugun_gelen,
                          bugun_hizmetler=bugun_hizmetler)


@app.route('/randevu_sil', methods=['POST'])
@login_required
def randevu_sil():
    randevu_id = request.form.get('randevu_id')
    sebep = request.form.get('sebep')

    if not randevu_id or not sebep:
        flash("Randevu ID veya silme sebebi eksik!", "danger")
        return redirect(url_for('randevular'))

    try:
        with closing(get_db_connection()) as conn:
            conn.execute('DELETE FROM randevular WHERE id = ?', (randevu_id,))
            conn.commit()
        flash("Randevu başarıyla silindi!", "success")
    except Exception as e:
        logger.error("Randevu silme sırasında hata: %s", e)
        flash(f"Bir hata oluştu: {str(e)}", "danger")

    return redirect(url_for('randevular'))

@app.route('/randevu_duzenle/<int:randevu_id>', methods=['GET', 'POST'])
@login_required
def randevu_duzenle(randevu_id):
    try:
        with closing(get_db_connection()) as conn:
            randevu = conn.execute(''' 
                SELECT randevular.id, müşteriler.ad, randevular.tarih, randevular.saat, 
                       randevular.hizmet, randevular.durum 
                FROM randevular 
                INNER JOIN müşteriler ON randevular.musteri_id = müşteriler.id 
                WHERE randevular.id = ? 
            ''', (randevu_id,)).fetchone()
    except Exception as e:
        logger.error("Randevu bilgisi çekilirken hata: %s", e)
        flash('Randevu bulunamadı!', 'danger')
        return redirect(url_for('randevular'))
    
    if not randevu:
        flash('Randevu bulunamadı!', 'danger')
        return redirect(url_for('randevular'))
    
    try:
        with closing(get_db_connection()) as conn:
            hizmetler = conn.execute('SELECT * FROM hizmetler').fetchall()
            
            # Randevu saatlerini otomatik oluştur
            randevu_saatleri = generate_randevu_saatleri()
    except Exception as e:
        logger.error("Hizmet listesi çekilirken hata: %s", e)
        flash(f'Hata: {str(e)}', 'danger')
        hizmetler = []
        randevu_saatleri = generate_randevu_saatleri()

    if request.method == 'POST':
        yeni_tarih = request.form.get('tarih', '')
        yeni_saat = request.form.get('saat', '')
        yeni_hizmet = request.form.get('hizmet', '')
        yeni_durum = request.form.get('durum', 'Bekleniyor')
        try:
            with closing(get_db_connection()) as conn:
                with conn:
                    conn.execute(
                        'UPDATE randevular SET tarih = ?, saat = ?, hizmet = ?, durum = ? WHERE id = ?',
                        (yeni_tarih, yeni_saat, yeni_hizmet, yeni_durum, randevu_id)
                    )
            flash('Randevu başarıyla güncellendi.', 'success')
            return redirect(url_for('randevular'))
        except Exception as e:
            logger.error("Randevu güncelleme hatası: %s", e)
            flash(f'Hata: {str(e)}', 'danger')

    return render_template('randevu_duzenle.html', randevu=randevu, hizmetler=hizmetler, randevu_saatleri=randevu_saatleri)

from datetime import datetime

@app.route('/api/randevu_durum_guncelle/<int:randevu_id>', methods=['PUT'])
def randevu_durum_guncelle(randevu_id):
    data = request.get_json()
    yeni_durum = data.get('durum')
    calisan_id = data.get('calisan_id')
    zaman = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with closing(get_db_connection()) as conn:
        # Randevu bilgilerini çek
        randevu = conn.execute(
            "SELECT musteri_id, hizmet FROM randevular WHERE id = ?", (randevu_id,)
        ).fetchone()
        if yeni_durum == "Geldi" and calisan_id:
            conn.execute(
                "UPDATE randevular SET durum = ?, çalışan_id = ?, durum_guncelleme_zamani = ? WHERE id = ?",
                (yeni_durum, calisan_id, zaman, randevu_id)
            )
            # Randevu hizmetiyle eşleşen ve kalan_seans > 0 olan satışın kalan_seans'ını azalt
            if randevu:
                # Önce hizmet adına karşılık gelen hizmet ID'sini bul
                hizmet_id = conn.execute(
                    "SELECT id FROM hizmetler WHERE hizmet_adi = ?", 
                    (randevu['hizmet'],)
                ).fetchone()
                
                if hizmet_id:
                    # Müşterinin bu hizmete ait ve kalan seansı olan satışını bul
                    satis = conn.execute(
                        """
                        SELECT id FROM satislar
                        WHERE musteri_id = ? AND urun_id = ? AND kalan_seans > 0
                        ORDER BY tarih ASC
                        LIMIT 1
                        """,
                        (randevu['musteri_id'], hizmet_id['id'])
                    ).fetchone()
                    
                    if satis:
                        conn.execute(
                            "UPDATE satislar SET kalan_seans = kalan_seans - 1 WHERE id = ?",
                            (satis['id'],)
                        )
        else:
            conn.execute(
                "UPDATE randevular SET durum = ?, durum_guncelleme_zamani = ? WHERE id = ?",
                (yeni_durum, zaman, randevu_id)
            )
        conn.commit()
    return jsonify({"message": "Durum güncellendi."})

 
@app.route('/api/randevu_tarih_guncelle/<int:randevu_id>', methods=['PUT'])
def randevu_tarih_guncelle(randevu_id):
    data = request.get_json()
    yeni_tarih = data.get('tarih')
    yeni_saat = data.get('saat')
    
    if not yeni_tarih or not yeni_saat:
        return jsonify({'status': 'error', 'message': 'Tarih ve saat gerekli'}), 400
    
    try:
        with closing(get_db_connection()) as conn:
            conn.execute(
                "UPDATE randevular SET tarih = ?, saat = ? WHERE id = ?",
                (yeni_tarih, yeni_saat, randevu_id)
            )
            conn.commit()
        return jsonify({'status': 'success', 'message': 'Randevu güncellendi'})
    except Exception as e:
        logger.error("Randevu güncelleme hatası: %s", e)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/gelir_gider_ekle', methods=['GET', 'POST'])
@login_required
def gelir_gider_ekle():
    if request.method == 'POST':
        tarih = request.form.get('tarih', datetime.now().strftime('%Y-%m-%d'))
        tur = request.form.get('tur')  # "Gelir" veya "Gider"
        tutar = request.form.get('tutar')
        odeme_sekli = request.form.get('odeme_sekli')
        aciklama = request.form.get('aciklama', '')
        
        if not (tutar and tur):
            flash("Tutar ve işlem türü boş bırakılamaz!", "danger")
            return redirect(url_for('gelir_gider_ekle'))
        
        try:
            tutar = float(tutar)
            now_time = datetime.now().strftime('%H:%M:%S')
            tarih_full = f"{tarih} {now_time}"
            with closing(get_db_connection()) as conn:
                with conn:
                    conn.execute(
    "INSERT INTO gelir_gider (tarih, tur, tutar, odeme_sekli, aciklama) VALUES (?, ?, ?, ?, ?)",
        (tarih_full, tur, tutar, odeme_sekli, aciklama)

                )
            flash("İşlem başarıyla kaydedildi!", "success")
            return redirect(url_for('gelir_gider_defteri'))
        except Exception as e:
            logger.error("Gelir/Gider ekleme hatası: %s", e)
            flash(f"Bir hata oluştu: {str(e)}", "danger")
    
    return render_template('gelir_gider_ekle.html')

@app.route('/gelir_gider_defteri', methods=['GET', 'POST'])
@login_required
def gelir_gider_defteri():
    try:
        tarih_baslangic = request.form.get('tarih_baslangic', '')
        tarih_bitis = request.form.get('tarih_bitis', '')
        tur_filtre = request.form.get('tur', '')

        query = """
            SELECT g.*, m.ad as musteri_adi 
            FROM gelir_gider g
            LEFT JOIN müşteriler m ON g.musteri_id = m.id
            WHERE 1=1
        """
        params = []

        if tarih_baslangic:
            query += " AND g.tarih >= ?"
            params.append(tarih_baslangic)
        if tarih_bitis:
            query += " AND g.tarih <= ?"
            params.append(tarih_bitis)
        if tur_filtre:
            query += " AND g.tur = ?"
            params.append(tur_filtre)

        query += " ORDER BY g.tarih DESC"

        with closing(get_db_connection()) as conn:
            transactions = conn.execute(query, params).fetchall()
            toplam_gelir = conn.execute("SELECT SUM(tutar) FROM gelir_gider WHERE tur = 'Gelir'").fetchone()[0] or 0
            toplam_gider = conn.execute("SELECT SUM(tutar) FROM gelir_gider WHERE tur = 'Gider'").fetchone()[0] or 0
            net_bakiye = toplam_gelir - toplam_gider

        return render_template('gelir_gider_defteri.html', 
                               transactions=transactions, 
                               toplam_gelir=toplam_gelir, 
                               toplam_gider=toplam_gider, 
                               net_bakiye=net_bakiye,
                               tarih_baslangic=tarih_baslangic,
                               tarih_bitis=tarih_bitis,
                               tur_filtre=tur_filtre)
    except Exception as e:
        logger.error("Gelir/Gider defteri çekilirken hata: %s", e)
        flash(f"Defter verileri alınamadı: {str(e)}", "danger")
        return redirect(url_for('index'))


@app.template_filter('saat_format')
def saat_format(value):
    try:
        return datetime.strptime(value, '%H:%M').strftime('%H:%M')
    except Exception as e:
        logger.error("Saat formatlama hatası: %s", e)
        return value

@app.route('/tahsilat_ekle/<int:musteri_id>', methods=['POST'])
def tahsilat_ekle(musteri_id):
    if request.method == 'POST':
        tutar = request.form.get('tutar', 0, type=float)
        aciklama = request.form.get('aciklama', '')
        odeme_sekli = request.form.get('odeme_sekli', '')
        taksit_id = request.form.get('taksit_id', None, type=int)
        satis_id = request.form.get('satis_id', None, type=int)
        kismi_odeme = request.form.get('kismi_odeme', 0, type=int)
        
        try:
            with closing(get_db_connection()) as conn:
                taksit_tutar = None
                taksit_bilgisi = None
                taksit_sira = None
                
                if taksit_id:
                    taksit_bilgisi = conn.execute(
                        'SELECT satis_id, tutar, tarih, son_odeme_tarihi, aciklama, sira FROM taksitler WHERE id = ?', 
                        (taksit_id,)
                    ).fetchone()
                    if taksit_bilgisi:
                        satis_id = satis_id or taksit_bilgisi['satis_id']
                        taksit_tutar = taksit_bilgisi['tutar']
                        taksit_sira = taksit_bilgisi['sira']
                
                # Açıklamayı peşinat ve taksit sırasına göre güncelle
                if taksit_sira == 1:
                    # 1. taksit -> Peşinat
                    if aciklama:
                        aciklama = aciklama.replace("1. Taksit", "Peşinat")
                    else:
                        aciklama = "Peşinat"
                elif taksit_sira and taksit_sira > 1:
                    yeni_sira = taksit_sira - 1
                    if aciklama:
                        aciklama = aciklama.replace(f"{taksit_sira}. Taksit", f"{yeni_sira}. Taksit")
                    else:
                        aciklama = f"{yeni_sira}. Taksit"
                
                # --- BURADA ACİKLAMA BİLGİSİ GÜNCELENİYOR ---
                if taksit_bilgisi:
                    taksit_aciklama = taksit_bilgisi['aciklama'] or ''
                    if not aciklama:
                        aciklama = taksit_aciklama
                    else:
                        if taksit_aciklama not in aciklama:
                            aciklama = f"{aciklama} - {taksit_aciklama}"
                
                # Tahsilat kaydı oluştur
                conn.execute(
                    '''INSERT INTO tahsilatlar (musteri_id, tutar, tarih, odeme_sekli, aciklama, satis_id, taksit_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (musteri_id, tutar, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), odeme_sekli, aciklama, satis_id, taksit_id)
                )
                
                # Kısmi ödeme ve taksit güncellemeleri
                if taksit_id and taksit_bilgisi:
                    # Önce taksiti ödenmiş olarak işaretle
                    conn.execute('UPDATE taksitler SET odendi = 1 WHERE id = ?', (taksit_id,))
                    
                    if kismi_odeme and tutar < taksit_tutar:
                        kalan_tutar = taksit_tutar - tutar
                        
                        try:
                            son_odeme_date = datetime.strptime(taksit_bilgisi['son_odeme_tarihi'], '%Y-%m-%d')
                            son_odeme = (son_odeme_date + timedelta(days=30)).strftime('%Y-%m-%d')
                        except:
                            son_odeme = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
                        
                        # Hizmet adını çek
                        urun_adi = ''
                        if satis_id:
                            urun = conn.execute('''
                                SELECT h.hizmet_adi FROM satislar s
                                LEFT JOIN hizmetler h ON h.id = s.urun_id
                                WHERE s.id = ?
                            ''', (satis_id,)).fetchone()
                            if urun:
                                urun_adi = urun['hizmet_adi']
                        
                        # Kısmi ödeme açıklaması oluştur
                        if taksit_sira == 0:  # 0 ise peşinat
                            kalan_aciklama = f"{urun_adi} - Peşinat (Kısmi Ödeme: {tutar:.0f}₺/{taksit_tutar:.0f}₺)"
                        else:
                            kalan_aciklama = f"{urun_adi} - {taksit_sira}. Taksit (Kısmi Ödeme: {tutar:.0f}₺/{taksit_tutar:.0f}₺)"

                        # Yeni kalan taksit kaydı oluştur
                        conn.execute(
                            '''INSERT INTO taksitler (satis_id, tutar, tarih, son_odeme_tarihi, aciklama, odendi, sira, orijinal_taksit_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                            (satis_id, kalan_tutar, datetime.now().strftime('%Y-%m-%d'), son_odeme, kalan_aciklama, 0, taksit_sira, taksit_id)
                        )
                        
                        # Tahsilat açıklamasını güncelle
                        aciklama = kalan_aciklama

                
                # Satış ve taksit durumu kontrolleri
                if satis_id and not taksit_id:
                    # Taksitsiz satış için kısmi ödeme kontrolü
                    if kismi_odeme and satis_id:
                        # Satışın toplam tutarını çek
                        satis_bilgisi = conn.execute('SELECT fiyat FROM satislar WHERE id = ?', (satis_id,)).fetchone()
                        if satis_bilgisi and 'fiyat' in satis_bilgisi.keys():
                            satis_tutari = satis_bilgisi['fiyat']
                            if tutar < satis_tutari:
                                # Kısmi ödeme yapıldı, satışı ödendi olarak işaretleme
                                kalan_tutar = satis_tutari - tutar
                                son_odeme = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
                                
                                # Kalan tutar için taksit oluştur
                                conn.execute(
                                    '''INSERT INTO taksitler (satis_id, tutar, tarih, son_odeme_tarihi, aciklama, odendi, sira)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                                    (satis_id, kalan_tutar, datetime.now().strftime('%Y-%m-%d'), son_odeme, 
                                     f"Taksitsiz Satış - Kalan Tutar (Kısmi Ödeme: {tutar:.0f}₺/{satis_tutari:.0f}₺)", 0, 1)
                                )
                    
                    # Tam ödeme yapıldığında satışı ödendi olarak işaretle
                    conn.execute('UPDATE satislar SET odendi = 1 WHERE id = ?', (satis_id,))
                
                if satis_id:
                    toplam_taksit = conn.execute('SELECT COUNT(*) FROM taksitler WHERE satis_id = ?', (satis_id,)).fetchone()[0]
                    if toplam_taksit > 0:
                        odenen_taksit = conn.execute('SELECT COUNT(*) FROM taksitler WHERE satis_id = ? AND odendi = 1', (satis_id,)).fetchone()[0]
                        if toplam_taksit == odenen_taksit:
                            conn.execute('UPDATE satislar SET odendi = 1 WHERE id = ?', (satis_id,))
                
                # Müşteri bakiyesini güncelle
                # Taksit ödemelerinde her zaman güncelle, manuel tahsilatlarda sadece checkbox işaretliyse
                if taksit_id or satis_id:
                    # Taksit veya satış ödemesi - her zaman bakiyeyi güncelle
                    conn.execute('UPDATE müşteriler SET bakiye = bakiye + ? WHERE id = ?', (tutar, musteri_id))
                else:
                    # Manuel tahsilat - sadece checkbox işaretliyse güncelle
                    bakiye_guncelle = request.form.get('bakiye_guncelle', 0, type=int)
                    if bakiye_guncelle:
                        conn.execute('UPDATE müşteriler SET bakiye = bakiye + ? WHERE id = ?', (tutar, musteri_id))
                
                # Gelir-gider tablosuna ekle
                kasa_gelir = request.form.get('kasa_gelir', 0, type=int)
                if kasa_gelir:
                    now_time = datetime.now().strftime('%H:%M:%S')
                    tarih_full = f"{datetime.now().strftime('%Y-%m-%d')} {now_time}"
                    conn.execute(
                        '''INSERT INTO gelir_gider (tarih, tur, tutar, odeme_sekli, aciklama, musteri_id)
                           VALUES (?, ?, ?, ?, ?, ?)''',
                        (tarih_full, 'Gelir', tutar, odeme_sekli, aciklama, musteri_id)
                    )
                
                conn.commit()
            flash('Tahsilat başarıyla kaydedildi!', 'success')
        except Exception as e:
            logger.error(f"Tahsilat eklenirken hata: {str(e)}")
            flash(f'Hata oluştu: {str(e)}', 'danger')
        
        return redirect(url_for('musteri_detay', musteri_id=musteri_id))

@app.route('/stoklar', methods=['GET', 'POST'])
def stoklar():
    try:
        with closing(get_db_connection()) as conn:
            stoklar = conn.execute('SELECT * FROM stoklar ORDER BY urun_adi').fetchall()
        return render_template('stoklar.html', stoklar=stoklar)
    except Exception as e:
        logger.error("Stok listesi çekilirken hata: %s", e)
        flash(f"Stok verileri alınamadı: {str(e)}", "danger")
        return redirect(url_for('index'))
#Stok yönetimi
@app.route('/stok_ekle', methods=['GET', 'POST'])
@login_required
def stok_ekle():
    if request.method == 'POST':
        urun_adi = request.form.get('urun_adi', '').strip()
        stok_adeti = request.form.get('stok_adeti', 0, type=int)
        satin_alinan_firma = request.form.get('satin_alinan_firma', '').strip()
        firma_listesi = []
    with closing(get_db_connection()) as conn:
        rows = conn.execute("SELECT DISTINCT satin_alinan_firma FROM stoklar WHERE satin_alinan_firma IS NOT NULL AND satin_alinan_firma != ''").fetchall()
        firma_listesi = [row['satin_alinan_firma'] for row in rows]
    if request.method == 'POST':
        birim_cinsi = request.form.get('birim_cinsi', '').strip()

        if not urun_adi or not birim_cinsi:
            flash("Ürün adı ve birim cinsi boş bırakılamaz!", "danger")
            return redirect(url_for('stok_ekle'))

        try:
            with closing(get_db_connection()) as conn:
                with conn:
                    conn.execute('''
                        INSERT INTO stoklar (urun_adi, stok_adeti, satin_alinan_firma, birim_cinsi)
                        VALUES (?, ?, ?, ?)
                    ''', (urun_adi, stok_adeti, satin_alinan_firma, birim_cinsi))
            flash("Stok başarıyla eklendi!", "success")
            return redirect(url_for('stoklar'))
        except Exception as e:
            logger.error("Stok ekleme hatası: %s", e)
            flash(f"Hata oluştu: {str(e)}", "danger")
            pass
    return render_template('stok_ekle.html')

@app.route('/satis_yap/<int:musteri_id>', methods=['POST'])
def satis_yap(musteri_id):
    urun_id = request.form.get('urun_id')
    miktar = request.form.get('miktar', type=int)
    fiyat = request.form.get('fiyat', type=float)
    aciklama = request.form.get('aciklama', '').strip()
    toplam_seans = request.form.get('toplam_seans', type=int, default=1)
    kalan_seans = toplam_seans  # Satış anında kalan seans, toplam seansa eşit başlar

    if not urun_id or not miktar or miktar <= 0 or fiyat is None or fiyat < 0 or toplam_seans < 1:
        flash("Geçerli bir ürün, miktar, fiyat ve seans sayısı girmelisiniz!", "danger")
        return redirect(url_for('musteri_detay', musteri_id=musteri_id))

    try:
        with closing(get_db_connection()) as conn:
            stok = conn.execute("SELECT stok_adeti FROM stoklar WHERE id = ?", (urun_id,)).fetchone()
            if not stok or stok['stok_adeti'] < miktar:
                flash("Yeterli stok bulunmamaktadır!", "danger")
                return redirect(url_for('musteri_detay', musteri_id=musteri_id))

            conn.execute("UPDATE stoklar SET stok_adeti = stok_adeti - ? WHERE id = ?", (miktar, urun_id))
            conn.execute('''
                INSERT INTO satislar (musteri_id, urun_id, miktar, fiyat, aciklama, tarih, toplam_seans, kalan_seans)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (musteri_id, urun_id, miktar, fiyat, aciklama, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), toplam_seans, kalan_seans))
            conn.execute("UPDATE müşteriler SET bakiye = bakiye - ? WHERE id = ?", (fiyat, musteri_id))
            conn.commit()
        flash("Satış ve seans kaydı başarıyla tamamlandı!", "success")
    except Exception as e:
        logger.error("Satış işlemi sırasında hata: %s", e)
        flash(f"Bir hata oluştu: {str(e)}", "danger")

    return redirect(url_for('musteri_detay', musteri_id=musteri_id))



@app.route('/dashboard')
@login_required
def dashboard():
    try:
        with closing(get_db_connection()) as conn:
            toplam_musteri = conn.execute("SELECT COUNT(*) FROM müşteriler").fetchone()[0] or 0
            toplam_randevu = conn.execute("SELECT COUNT(*) FROM randevular").fetchone()[0] or 0
            bekleyen_randevu = conn.execute("SELECT COUNT(*) FROM randevular WHERE durum = 'Bekleniyor'").fetchone()[0] or 0

            gelen_randevu = conn.execute("SELECT COUNT(*) FROM randevular WHERE durum = 'Geldi'").fetchone()[0] or 0
            gelmeyen_randevu = conn.execute("SELECT COUNT(*) FROM randevular WHERE durum = 'Gelmedi'").fetchone()[0] or 0
            toplam_gelir = conn.execute("SELECT SUM(tutar) FROM gelir_gider WHERE tur = 'Gelir'").fetchone()[0] or 0
            toplam_gider = conn.execute("SELECT SUM(tutar) FROM gelir_gider WHERE tur = 'Gider'").fetchone()[0] or 0
            net_bakiye = toplam_gelir - toplam_gider
        return render_template('dashboard.html',
                               toplam_musteri=toplam_musteri,
                               toplam_randevu=toplam_randevu,
                               bekleyen_randevu=bekleyen_randevu,
                               gelen_randevu=gelen_randevu,
                               gelmeyen_randevu=gelmeyen_randevu,
                               toplam_gelir=toplam_gelir,
                               toplam_gider=toplam_gider,
                               net_bakiye=net_bakiye)
    except Exception as e:
        flash(f"Dashboard verileri alınamadı: {str(e)}", "danger")
        return redirect(url_for('index'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    next_page = request.args.get('next') or request.form.get('next')
    if request.method == 'POST':
        kullanici_adi = request.form.get('kullanici_adi')
        sifre = request.form.get('sifre')
        user = Kullanici.get_by_username(kullanici_adi)
        if user and check_password_hash(user.sifre_hash, sifre):
            login_user(user)
            flash('Giriş başarılı!', 'success')
            return redirect(next_page or url_for('index'))
        else:
            flash('Kullanıcı adı veya şifre hatalı!', 'danger')
    return render_template('login.html', year=datetime.now().year, next=next_page)

@app.route('/logout')
def logout():
    session.clear()
    next_page = request.args.get('next')
    if next_page:
        return redirect(next_page)
    return redirect(url_for('login'))

# Yönetici için kullanıcı ekleme (örnek)
@app.route('/kullanici_ekle', methods=['GET', 'POST'])
@login_required
def kullanici_ekle():
    if current_user.rol != 'admin':
        flash('Yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
    if request.method == 'POST':
        kullanici_adi = request.form.get('kullanici_adi')
        sifre = request.form.get('sifre')
        rol = request.form.get('rol', 'personel')
        sifre_hash = generate_password_hash(sifre)
        try:
            with closing(get_db_connection()) as conn:
                with conn:
                    conn.execute(
                        "INSERT INTO kullanicilar (kullanici_adi, sifre_hash, rol) VALUES (?, ?, ?)",
                        (kullanici_adi, sifre_hash, rol)
                    )
            flash('Kullanıcı başarıyla eklendi.', 'success')
            return redirect(url_for('index'))
        except sqlite3.IntegrityError:
            flash('Bu kullanıcı adı zaten mevcut!', 'danger')
    return render_template('kullanici_ekle.html')

@app.route('/kullanici_duzenle/<int:kullanici_id>', methods=['GET', 'POST'])
@login_required
def kullanici_duzenle(kullanici_id):
    # Kullanıcıyı veritabanından bul, form ile düzenle, kaydet
    # ...
    return render_template('kullanici_duzenle.html', kullanici_id=kullanici_id)

@app.route('/kullanici_sil/<int:kullanici_id>', methods=['POST'])
@login_required
def kullanici_sil(kullanici_id):
    with closing(get_db_connection()) as conn:
        conn.execute("DELETE FROM kullanicilar WHERE id = ?", (kullanici_id,))
        conn.commit()
    flash('Kullanıcı başarıyla silindi.', 'success')
    return redirect(url_for('kullanici_yonetimi'))

@app.route('/sadece_admin')
@login_required
def sadece_admin():
    if current_user.rol != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
    # admin işlemleri burada
    return render_template('sadece_admin.html')

@app.route('/tahsilat_listesi')
@login_required
def tahsilat_listesi():
    if current_user.rol != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
    
    try:
        with closing(get_db_connection()) as conn:
            tahsilatlar = conn.execute('''
                SELECT t.*, m.ad as musteri_adi, tk.son_odeme_tarihi
                FROM tahsilatlar t
                LEFT JOIN müşteriler m ON t.musteri_id = m.id
                LEFT JOIN taksitler tk ON t.taksit_id = tk.id
                ORDER BY t.tarih DESC
            ''').fetchall()
            
        return render_template('tahsilat_listesi.html', 
                              tahsilatlar=tahsilatlar,
                              current_year=datetime.now().year)
    except Exception as e:
        logger.error(f"Tahsilat listesi çekilirken hata: {str(e)}")
        flash(f"Veri alınamadı: {str(e)}", "danger")
        return redirect(url_for('index'))

@app.route('/kullanici_yonetimi')
@login_required
def kullanici_yonetimi():
    if current_user.rol != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
    with closing(get_db_connection()) as conn:
        kullanicilar = conn.execute("SELECT * FROM kullanicilar").fetchall()
    return render_template('kullanici_yonetimi.html', kullanicilar=kullanicilar)

@app.route('/sistem_ayarlar', methods=['GET', 'POST'])
@login_required
def sistem_ayarlar():
    if current_user.rol != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
        
    # Sürüm bilgisini şablona gönder
    global SURUM
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        try:
            with closing(get_db_connection()) as conn:
                if action == 'genel':
                    isletme_adi = request.form.get('isletme_adi', '')
                    telefon = request.form.get('telefon', '')
                    adres = request.form.get('adres', '')
                    
                    conn.execute('''
                        INSERT OR REPLACE INTO sistem_ayarlar (anahtar, deger)
                        VALUES ('isletme_adi', ?), ('telefon', ?), ('adres', ?)
                    ''', (isletme_adi, telefon, adres))
                    
                elif action == 'randevu':
                    calisma_baslangic = request.form.get('calisma_baslangic', '09:00')
                    calisma_bitis = request.form.get('calisma_bitis', '18:00')
                    randevu_araligi = request.form.get('randevu_araligi', '30')
                    
                    conn.execute('''
                        INSERT OR REPLACE INTO sistem_ayarlar (anahtar, deger)
                        VALUES ('calisma_baslangic', ?), ('calisma_bitis', ?), ('randevu_araligi', ?)
                    ''', (calisma_baslangic, calisma_bitis, randevu_araligi))
                    
                elif action == 'bildirim':
                    whatsapp_aktif = 1 if request.form.get('whatsapp_aktif') else 0
                    email_aktif = 1 if request.form.get('email_aktif') else 0
                    hatirlatma_suresi = request.form.get('hatirlatma_suresi', '2')
                    
                    conn.execute('''
                        INSERT OR REPLACE INTO sistem_ayarlar (anahtar, deger)
                        VALUES ('whatsapp_aktif', ?), ('email_aktif', ?), ('hatirlatma_suresi', ?)
                    ''', (str(whatsapp_aktif), str(email_aktif), hatirlatma_suresi))
                    
                elif action == 'finansal':
                    para_birimi = request.form.get('para_birimi', 'TL')
                    kdv_orani = request.form.get('kdv_orani', '18')
                    otomatik_fatura = 1 if request.form.get('otomatik_fatura') else 0
                    
                    conn.execute('''
                        INSERT OR REPLACE INTO sistem_ayarlar (anahtar, deger)
                        VALUES ('para_birimi', ?), ('kdv_orani', ?), ('otomatik_fatura', ?)
                    ''', (para_birimi, kdv_orani, str(otomatik_fatura)))
                
                conn.commit()
                flash('Ayarlar başarıyla kaydedildi!', 'success')
        except Exception as e:
            logger.error(f"Sistem ayarları kaydetme hatası: {str(e)}")
            flash(f'Hata oluştu: {str(e)}', 'danger')
    
    # Ayarları çek
    try:
        with closing(get_db_connection()) as conn:
            ayarlar_rows = conn.execute('SELECT anahtar, deger FROM sistem_ayarlar').fetchall()
            ayarlar = {row['anahtar']: row['deger'] for row in ayarlar_rows}
            
            # Boolean değerleri dönüştür
            ayarlar['whatsapp_aktif'] = ayarlar.get('whatsapp_aktif') == '1'
            ayarlar['email_aktif'] = ayarlar.get('email_aktif') == '1'
            ayarlar['otomatik_fatura'] = ayarlar.get('otomatik_fatura') == '1'
            
            # İstatistikler
            toplam_musteri = conn.execute('SELECT COUNT(*) FROM müşteriler').fetchone()[0]
            toplam_randevu = conn.execute('SELECT COUNT(*) FROM randevular').fetchone()[0]
            
    except Exception as e:
        logger.error(f"Sistem ayarları çekme hatası: {str(e)}")
        ayarlar = {}
        toplam_musteri = 0
        toplam_randevu = 0
    
    return render_template('sistem_ayarlar.html', 
                         ayarlar=ayarlar,
                         toplam_musteri=toplam_musteri,
                         toplam_randevu=toplam_randevu,
                         son_guncelleme=datetime.now().strftime('%d.%m.%Y'),
                         SURUM=SURUM)

from datetime import datetime, timedelta

@app.route('/raporlar')
@login_required
def raporlar():
    if current_user.rol != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
    with closing(get_db_connection()) as conn:
        toplam_musteri = conn.execute("SELECT COUNT(*) FROM müşteriler").fetchone()[0] or 0
        toplam_gelir = conn.execute("SELECT SUM(tutar) FROM gelir_gider WHERE tur = 'Gelir'").fetchone()[0] or 0
        toplam_gider = conn.execute("SELECT SUM(tutar) FROM gelir_gider WHERE tur = 'Gider'").fetchone()[0] or 0
        bekleyen_randevu = conn.execute("SELECT COUNT(*) FROM randevular WHERE durum = 'Bekleniyor'").fetchone()[0] or 0
        gelen_randevu = conn.execute("SELECT COUNT(*) FROM randevular WHERE durum = 'Geldi'").fetchone()[0] or 0
        gelmeyen_randevu = conn.execute("SELECT COUNT(*) FROM randevular WHERE durum = 'Gelmedi'").fetchone()[0] or 0
        populer_hizmetler = conn.execute("""
            SELECT hizmet AS hizmet_adi, COUNT(*) AS toplam
            FROM randevular
            GROUP BY hizmet
            ORDER BY toplam DESC
            LIMIT 5
        """).fetchall()
        kritik_stoklar = conn.execute("""
            SELECT urun_adi, stok_adeti, birim_cinsi
            FROM stoklar
            WHERE stok_adeti <= 5
            ORDER BY stok_adeti ASC
            LIMIT 10
        """).fetchall()
        musteri_hareketleri = conn.execute("""
            SELECT kayit_tarihi AS tarih, ad AS musteri, 'Kayıt' AS islem, notlar AS aciklama
            FROM müşteriler
            ORDER BY kayit_tarihi DESC
            LIMIT 100
        """).fetchall()

        # Son 12 ay için etiket ve veriler
        aylik_labels = []
        aylik_gelir = []
        aylik_gider = []
        aylik_musteri = []
        aylik_randevu = []

        now = datetime.now()
        for i in range(11, -1, -1):
            ay = (now.month - i - 1) % 12 + 1
            yil = now.year if now.month - i > 0 else now.year - 1
            label = f"{yil}-{ay:02d}"  # Sorgular için
            aylik_labels.append(f"{ay:02d}-{yil}")  # Grafik etiketi için

            # Gelir
            gelir = conn.execute(
                "SELECT SUM(tutar) FROM gelir_gider WHERE tur = 'Gelir' AND strftime('%Y-%m', tarih) = ?",
                (label,)
            ).fetchone()[0] or 0
            aylik_gelir.append(gelir)

            # Gider
            gider = conn.execute(
                "SELECT SUM(tutar) FROM gelir_gider WHERE tur = 'Gider' AND strftime('%Y-%m', tarih) = ?",
                (label,)
            ).fetchone()[0] or 0
            aylik_gider.append(gider)

            # Yeni müşteri
            musteri = conn.execute(
                "SELECT COUNT(*) FROM müşteriler WHERE strftime('%Y-%m', kayit_tarihi) = ?",
                (label,)
            ).fetchone()[0] or 0
            aylik_musteri.append(musteri)

            # Randevu
            randevu = conn.execute(
                "SELECT COUNT(*) FROM randevular WHERE strftime('%Y-%m', tarih) = ?",
                (label,)
            ).fetchone()[0] or 0
            aylik_randevu.append(randevu)

    return render_template(
        'raporlar.html',
        toplam_musteri=toplam_musteri,
        toplam_gelir=toplam_gelir,
        toplam_gider=toplam_gider,
        bekleyen_randevu=bekleyen_randevu,
        gelen_randevu=gelen_randevu,
        gelmeyen_randevu=gelmeyen_randevu,
        populer_hizmetler=populer_hizmetler,
        kritik_stoklar=kritik_stoklar,
        aylik_labels=aylik_labels,
        aylik_gelir=aylik_gelir,
        aylik_gider=aylik_gider,
        aylik_musteri=aylik_musteri,
        aylik_randevu=aylik_randevu,
        musteri_hareketleri=musteri_hareketleri,

    )
    
@app.route('/calisan_yonetimi')
@login_required
def calisan_yonetimi():
    if current_user.rol != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
    with closing(get_db_connection()) as conn:
        calisanlar = conn.execute("SELECT * FROM çalışanlar").fetchall()
    return render_template('calisan_yonetimi.html', calisanlar=calisanlar)

@app.route('/calisan_hizmet_yonetimi')
@login_required
def calisan_hizmet_yonetimi():
    if current_user.rol != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
    
    try:
        with closing(get_db_connection()) as conn:
            # Tüm çalışanları getir
            calisanlar = conn.execute("SELECT * FROM çalışanlar ORDER BY ad").fetchall()
            
            # Tüm hizmetleri getir
            hizmetler = conn.execute("SELECT * FROM hizmetler ORDER BY hizmet_adi").fetchall()
            
            # Çalışan-hizmet ilişkilerini getir
            calisan_hizmet_rows = conn.execute('''
                SELECT ch.calisan_id, ch.hizmet_id, c.ad as calisan_adi, h.hizmet_adi
                FROM calisan_hizmet ch
                JOIN çalışanlar c ON ch.calisan_id = c.id
                JOIN hizmetler h ON ch.hizmet_id = h.id
                ORDER BY c.ad, h.hizmet_adi
            ''').fetchall()
            
            # Çalışan başına hizmetleri grupla
            calisanlar_hizmetler = []
            calisan_hizmet_dict = {}
            
            for calisan in calisanlar:
                calisan_id = calisan['id']
                calisan_hizmetleri = []
                calisan_hizmet_ids = []
                
                for row in calisan_hizmet_rows:
                    if row['calisan_id'] == calisan_id:
                        calisan_hizmetleri.append(row['hizmet_adi'])
                        calisan_hizmet_ids.append(row['hizmet_id'])
                
                calisanlar_hizmetler.append({
                    'id': calisan_id,
                    'ad': calisan['ad'],
                    'hizmetler': calisan_hizmetleri
                })
                
                calisan_hizmet_dict[str(calisan_id)] = calisan_hizmet_ids
            
            # JSON formatında çalışan-hizmet ilişkilerini hazırla
            import json
            calisan_hizmet_json = json.dumps(calisan_hizmet_dict)
            
        return render_template('calisan_hizmet_yonetimi.html', 
                              calisanlar=calisanlar,
                              hizmetler=hizmetler,
                              calisanlar_hizmetler=calisanlar_hizmetler,
                              calisan_hizmet_json=calisan_hizmet_json)
    except Exception as e:
        logger.error(f"Çalışan hizmet yönetimi hatası: {str(e)}")
        flash(f"Hata oluştu: {str(e)}", "danger")
        return redirect(url_for('index'))

@app.route('/calisan_hizmet_kaydet', methods=['POST'])
@login_required
def calisan_hizmet_kaydet():
    if current_user.rol != 'admin':
        flash('Bu işlemi yapmaya yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
    
    calisan_id = request.form.get('calisan_id')
    hizmet_ids = request.form.getlist('hizmet_ids')
    
    if not calisan_id:
        flash('Çalışan seçilmedi!', 'danger')
        return redirect(url_for('calisan_hizmet_yonetimi'))
    
    try:
        with closing(get_db_connection()) as conn:
            # Önce bu çalışanın tüm hizmet ilişkilerini sil
            conn.execute('DELETE FROM calisan_hizmet WHERE calisan_id = ?', (calisan_id,))
            
            # Yeni hizmet ilişkilerini ekle
            for hizmet_id in hizmet_ids:
                conn.execute(
                    'INSERT INTO calisan_hizmet (calisan_id, hizmet_id) VALUES (?, ?)',
                    (calisan_id, hizmet_id)
                )
            
            conn.commit()
        
        flash('Çalışan hizmet atamaları başarıyla güncellendi!', 'success')
    except Exception as e:
        logger.error(f"Çalışan hizmet kaydetme hatası: {str(e)}")
        flash(f"Hata oluştu: {str(e)}", "danger")
    
    return redirect(url_for('calisan_hizmet_yonetimi'))

@app.route('/log_kayitlari')
@login_required
def log_kayitlari():
    if current_user.rol != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
    
    try:
        
        # Log dosyasını oku
        import os
        log_dosyasi = os.path.join(os.getcwd(), 'kuafor_app.log')
        loglar = []
        
        if os.path.exists(log_dosyasi):
            with open(log_dosyasi, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        # Log satırını parçala
                        parts = line.strip().split(' - ')
                        if len(parts) >= 3:
                            tarih_zaman = parts[0]
                            log_seviyesi = parts[1]
                            mesaj = ' - '.join(parts[2:])
                            
                            # Log türünü belirle
                            tur = "info"
                            if "WARNING" in log_seviyesi:
                                tur = "warning"
                            elif "ERROR" in log_seviyesi or "CRITICAL" in log_seviyesi:
                                tur = "error"
                            
                            # Mesajdan tarih/saat bilgisini kaldır
                            # Mesajın başında tarih formatı varsa temizle
                            import re
                            # Tarih formatını temizle (YYYY-MM-DD HH:MM:SS)
                            temiz_mesaj = re.sub(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - ', '', mesaj)
                            
                            loglar.append({
                                "tarih": tarih_zaman,
                                "olay": temiz_mesaj,
                                "tur": tur
                            })
                    except Exception as e:
                        # Hatalı log satırlarını atla
                        continue
        
        # Logları tarihe göre sırala (en yeni en üstte)
        loglar.reverse()
        
        # Eğer log dosyası yoksa veya boşsa örnek loglar göster
        if not loglar:
            loglar = [
                {"tarih": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "olay": "Log kayıtları sayfası görüntülendi", "tur": "info"},
                {"tarih": (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"), "olay": "Bu bir test uyarısıdır", "tur": "warning"},
                {"tarih": (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"), "olay": "Bu bir test hata mesajıdır", "tur": "error"},
            ]
    except Exception as e:
        logger.error(f"Log kayıtları alınırken hata: {str(e)}")
        flash(f"Log kayıtları alınırken hata oluştu: {str(e)}", "danger")
        loglar = []
    
    return render_template('log_kayitlari.html', loglar=loglar)

@app.route('/log_temizle', methods=['POST'])
@login_required
def log_temizle():
    if current_user.rol != 'admin':
        return jsonify({'success': False, 'error': 'Yetkiniz yok!'})
    
    try:
        import os
        log_dosyasi = os.path.join(os.getcwd(), 'kuafor_app.log')
        
        # Log dosyasını temizle
        if os.path.exists(log_dosyasi):
            with open(log_dosyasi, 'w', encoding='utf-8') as f:
                f.write('')  # Dosyayı boşalt
        
        # Temizleme işlemini logla
        logger.info("Log dosyası temizlendi")
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Log temizleme hatası: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/hizmet_tanimlari')
@login_required
def hizmet_tanimlari():
    if current_user.rol != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
    with closing(get_db_connection()) as conn:
        # Hizmetleri getir
        hizmetler = conn.execute('SELECT * FROM hizmetler').fetchall()
        
        # Çalışanları getir
        calisanlar = conn.execute("SELECT * FROM çalışanlar ORDER BY ad").fetchall()
        
        # Hizmetlere çalışan bilgilerini ekle
        for i in range(len(hizmetler)):
            h = dict(hizmetler[i])
            # calisan_id sütunu varsa ve boş değilse, virgülle ayrılmış ID'leri listeye çevir
            if 'calisan_id' in h and h['calisan_id']:
                # Önce string'e çevir, çünkü int olabilir
                calisan_id_str = str(h['calisan_id'])
                h['calisan_ids'] = calisan_id_str.split(',')
            else:
                h['calisan_ids'] = []
            
            hizmetler[i] = h
    return render_template('hizmet_tanimlari.html', hizmetler=hizmetler, calisanlar=calisanlar)

@app.route('/bildirim_ayarlar', methods=['GET', 'POST'])
@login_required
def bildirim_ayarlar():
    if current_user.rol != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        whatsapp_sablon = request.form.get('whatsapp_sablon')
        online_whatsapp_sablon = request.form.get('online_whatsapp_sablon')
        
        set_whatsapp_sablon(whatsapp_sablon)
        set_online_whatsapp_sablon(online_whatsapp_sablon)
        
        flash('WhatsApp şablonları kaydedildi.', 'success')
    else:
        whatsapp_sablon = get_whatsapp_sablon()
        online_whatsapp_sablon = get_online_whatsapp_sablon()

    return render_template('bildirim_ayarlar.html', 
                          whatsapp_sablon=whatsapp_sablon,
                          online_whatsapp_sablon=online_whatsapp_sablon)

@app.route('/yetki_yonetimi')
@login_required
def yetki_yonetimi():
    if current_user.rol != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
    with closing(get_db_connection()) as conn:
        kullanicilar = conn.execute("SELECT * FROM kullanicilar").fetchall()
    return render_template('yetki_yonetimi.html', kullanicilar=kullanicilar)

@app.route('/destek')
@login_required
def destek():
    if current_user.rol != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
    return render_template('destek.html')

def update_payment_status():
    """
    Tüm satışların ödeme durumunu günceller.
    Bir satışın tüm taksitleri ödenmişse, satışı 'ödendi' olarak işaretler.
    """
    try:
        with closing(get_db_connection()) as conn:
            # Taksitli satışları bul
            taksitli_satislar = conn.execute('''
                SELECT DISTINCT satis_id 
                FROM taksitler
            ''').fetchall()
            
            for satis in taksitli_satislar:
                satis_id = satis['satis_id']
                
                # Satışa ait toplam taksit sayısı
                toplam_taksit = conn.execute('SELECT COUNT(*) FROM taksitler WHERE satis_id = ?', 
                                            (satis_id,)).fetchone()[0]
                
                # Satışa ait ödenen taksit sayısı
                odenen_taksit = conn.execute('SELECT COUNT(*) FROM taksitler WHERE satis_id = ? AND odendi = 1', 
                                            (satis_id,)).fetchone()[0]
                
                # Tüm taksitler ödendiyse satışı ödenmiş olarak işaretle
                if toplam_taksit == odenen_taksit and toplam_taksit > 0:
                    conn.execute('UPDATE satislar SET odendi = 1 WHERE id = ?', (satis_id,))
            
            conn.commit()
            logger.info("Ödeme durumları güncellendi.")
    except Exception as e:
        logger.error(f"Ödeme durumları güncellenirken hata: {str(e)}")




@app.route('/yedek')
@app.route('/yedek_al')
@login_required
def yedek():
    """Basit yedekleme fonksiyonu"""
    try:
        import os
        import shutil
        from flask import send_file
        
        # Veritabanı dosyasını bul
        db_files = [f for f in os.listdir('.') if f.endswith('.db')]
        if not db_files:
            flash('Veritabanı dosyası bulunamadı!', 'danger')
            return redirect(url_for('sistem_ayarlar'))
        
        db_file = db_files[0]  # İlk bulunan .db dosyasını kullan
        logger.info(f"Yedekleme işlemi başlatıldı: {db_file}")
        
        # Yedek dosya adı
        backup_filename = f"yedek_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        backup_path = os.path.join(os.getcwd(), backup_filename)
        
        # Veritabanını kopyala
        shutil.copy2(db_file, backup_path)
        logger.info(f"Veritabanı yedeklendi: {backup_path}")
        
        # Dosyayı indir
        return send_file(backup_path, as_attachment=True, download_name=backup_filename)
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Yedekleme hatası: {str(e)}\n{error_details}")
        flash(f'Yedekleme hatası: {str(e)}', 'danger')
        return redirect(url_for('sistem_ayarlar'))

@app.route('/yedek_yukle', methods=['POST'])
def yedek_yukle():
    """Yedek dosyasını yükleme fonksiyonu"""
    # Kullanıcı giriş yapmış mı kontrol et
    if not current_user.is_authenticated:
        flash('Bu işlemi yapmak için giriş yapmalısınız!', 'danger')
        return redirect(url_for('login'))
        
    # Admin yetkisi kontrolü
    if current_user.rol != 'admin':
        flash('Bu işlemi yapmaya yetkiniz yok!', 'danger')
        return redirect(url_for('sistem_ayarlar'))
    
    try:
        import os
        import shutil
        from werkzeug.utils import secure_filename
        
        # Yüklenen dosyayı kontrol et
        if 'backup_file' not in request.files:
            flash('Dosya seçilmedi!', 'danger')
            return redirect(url_for('sistem_ayarlar'))
        
        file = request.files['backup_file']
        if file.filename == '':
            flash('Dosya seçilmedi!', 'danger')
            return redirect(url_for('sistem_ayarlar'))
        
        if not file.filename.endswith('.db'):
            flash('Geçersiz dosya formatı! Sadece .db uzantılı dosyalar yüklenebilir.', 'danger')
            return redirect(url_for('sistem_ayarlar'))
        
        # Mevcut veritabanını bul
        db_files = [f for f in os.listdir('.') if f.endswith('.db')]
        if not db_files:
            flash('Mevcut veritabanı dosyası bulunamadı!', 'danger')
            return redirect(url_for('sistem_ayarlar'))
        
        db_file = db_files[0]  # İlk bulunan .db dosyasını kullan
        
        # Mevcut veritabanının yedeğini al
        backup_filename = f"otomatik_yedek_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        backup_path = os.path.join(os.getcwd(), backup_filename)
        shutil.copy2(db_file, backup_path)
        logger.info(f"Geri yükleme öncesi otomatik yedek alındı: {backup_path}")
        
        # Yüklenen dosyayı geçici olarak kaydet
        temp_path = os.path.join(os.getcwd(), 'temp_upload.db')
        file.save(temp_path)
        
        # Uygulamayı yeniden başlatmadan önce veritabanı bağlantılarını kapat
        # SQLite veritabanı dosyasını değiştirmeden önce tüm bağlantıları kapatmak önemli
        import sqlite3
        sqlite3.connect(':memory:').close()  # SQLite bağlantı havuzunu temizle
        
        # Yüklenen dosyayı mevcut veritabanının yerine koy
        shutil.copy2(temp_path, db_file)
        os.remove(temp_path)  # Geçici dosyayı sil
        
        logger.info(f"Veritabanı başarıyla geri yüklendi: {db_file}")
        flash('Veritabanı başarıyla geri yüklendi! Değişikliklerin tam olarak uygulanması için uygulamayı yeniden başlatmanız gerekebilir.', 'success')
        
        return redirect(url_for('sistem_ayarlar'))
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Yedek yükleme hatası: {str(e)}\n{error_details}")
        flash(f'Yedek yükleme hatası: {str(e)}', 'danger')
        return redirect(url_for('sistem_ayarlar'))
    
# Bu sayfa dış erişime açık, login_required dekoratörü yok
@app.route('/online_randevu', methods=['GET', 'POST'])
def online_randevu():
    try:
        with closing(get_db_connection()) as conn:
            # Hizmetleri getir
            hizmetler = [dict(row) for row in conn.execute('SELECT * FROM hizmetler').fetchall()]
            
            # Çalışanları getir
            calisanlar = conn.execute('SELECT * FROM çalışanlar').fetchall()
            
            # Randevu saatlerini otomatik oluştur
            randevu_saatleri = generate_randevu_saatleri()
            
            # Hizmetlere çalışan adlarını ekle
            for i in range(len(hizmetler)):
                h = hizmetler[i]
                if 'calisan_id' in h and h['calisan_id']:
                    for c in calisanlar:
                        if c['id'] == h['calisan_id']:
                            h['calisan_adi'] = c['ad']
                            break
                    if 'calisan_adi' not in h:
                        h['calisan_adi'] = None
                else:
                    h['calisan_adi'] = None

        if request.method == 'POST':
            ad = request.form.get('ad', '').strip().title()
            telefon = request.form.get('telefon', '').strip()
            hizmet_id = request.form.get('hizmet_id')
            tarih = request.form.get('tarih')
            saat = request.form.get('saat')
            secilen_calisan_id = request.form.get('calisan_id')

            if not (ad and telefon and hizmet_id and tarih and saat):
                flash("Tüm alanlar zorunludur!", "danger")
                return render_template('online_randevu.html', hizmetler=hizmetler, current_year=datetime.now().year)
                
            # Pazar günü kontrolü
            try:
                tarih_obj = datetime.strptime(tarih, '%Y-%m-%d')
                if tarih_obj.weekday() == 6:  # 6 = Pazar
                    flash("Pazar günleri salon kapalıdır, randevu oluşturulamaz!", "danger")
                    return render_template('online_randevu.html', hizmetler=hizmetler, randevu_saatleri=randevu_saatleri, current_year=datetime.now().year)
            except ValueError:
                flash("Geçersiz tarih formatı!", "danger")
                return render_template('online_randevu.html', hizmetler=hizmetler, randevu_saatleri=randevu_saatleri, current_year=datetime.now().year)

            with closing(get_db_connection()) as conn:
                # Hizmet bilgilerini al
                hizmet_row = conn.execute('SELECT * FROM hizmetler WHERE id = ?', (hizmet_id,)).fetchone()
                
                if not hizmet_row:
                    flash("Seçilen hizmet bulunamadı!", "danger")
                    return render_template('online_randevu.html', hizmetler=hizmetler, current_year=datetime.now().year)
                
                hizmet_adi = hizmet_row['hizmet_adi']
                seans = hizmet_row['seans'] if hizmet_row['seans'] else 1
                fiyat = hizmet_row['fiyat'] if 'fiyat' in hizmet_row.keys() else 0
                
                # Çalışan bilgilerini al
                calisan_id = None
                calisan_adi = 'Belirtilmemiş'
                
                # Eğer formdan çalışan seçilmişse, onu kullan
                if secilen_calisan_id:
                    # Seçilen çalışanın müsaitliğini kontrol et
                    var_mi = conn.execute(
                        'SELECT id FROM randevular WHERE tarih = ? AND saat = ? AND çalışan_id = ?',
                        (tarih, saat, secilen_calisan_id)
                    ).fetchone()
                    
                    if var_mi:
                        flash("Seçtiğiniz çalışan bu tarih ve saatte dolu!", "danger")
                        return render_template('online_randevu.html', hizmetler=hizmetler, current_year=datetime.now().year)
                    
                    # Çalışan adını al
                    calisan_row = conn.execute('SELECT ad FROM çalışanlar WHERE id = ?', (secilen_calisan_id,)).fetchone()
                    if calisan_row:
                        calisan_id = secilen_calisan_id
                        calisan_adi = calisan_row['ad']
                    else:
                        flash("Seçilen çalışan bulunamadı!", "danger")
                        return render_template('online_randevu.html', hizmetler=hizmetler, current_year=datetime.now().year)
                
                # Çalışan seçilmemişse, online randevuda çalışan ataması yapmıyoruz
                # Kullanıcıya çalışan seçmesi gerektiğini bildir
                if not calisan_id:
                    flash("Lütfen bir çalışan seçiniz!", "danger")
                    return render_template('online_randevu.html', hizmetler=hizmetler, randevu_saatleri=randevu_saatleri, current_year=datetime.now().year)
                
                # Online randevu olduğunu belirt
                hizmet_adi_online = f"{hizmet_adi} (online) - {calisan_adi}"

                # Müşteri kaydı
                musteri = conn.execute('SELECT id FROM müşteriler WHERE telefon = ?', (telefon,)).fetchone()
                if musteri:
                    musteri_id = musteri['id']
                else:
                    conn.execute(
                        'INSERT INTO müşteriler (ad, telefon) VALUES (?, ?)',
                        (ad, telefon)
                    )
                    musteri_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]

                # Randevu kaydı - çalışan ID'si ile birlikte
                conn.execute(
                    'INSERT INTO randevular (musteri_id, çalışan_id, tarih, saat, hizmet, seans, durum) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (musteri_id, calisan_id, tarih, saat, hizmet_adi_online, seans, 'Bekleniyor')
                )

                # Satış kaydı (otomatik)
                conn.execute(
                    '''INSERT INTO satislar (musteri_id, urun_id, miktar, fiyat, aciklama, tarih, toplam_seans, kalan_seans)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                    (musteri_id, hizmet_id, 1, fiyat, f'Online randevu ile otomatik satış - {calisan_adi}', tarih, seans, seans)
                )

                # >>> Müşteri bakiyesini güncelle (borçlandır) <<<
                conn.execute(
                    'UPDATE müşteriler SET bakiye = bakiye - ? WHERE id = ?', (fiyat, musteri_id)
                )

                conn.commit()

            # WhatsApp mesajı için telefon numarasını düzenle
            tel = ''.join(filter(str.isdigit, telefon))
            if len(tel) == 10:
                tel = '90' + tel
            elif len(tel) == 11 and tel.startswith('0'):
                tel = '9' + tel
                
            # Online randevu için WhatsApp şablonunu veritabanından çek
            try:
                sablon = get_online_whatsapp_sablon() or get_whatsapp_sablon() or "Sayın {ad}, {tarih} tarihinde saat {saat}'de {hizmet} randevunuz başarıyla oluşturuldu. Uzman: {calisan}. Teşekkürler!"
            except Exception as e:
                logger.error(f"WhatsApp şablonu alınırken hata: {str(e)}")
                sablon = "Sayın {ad}, {tarih} tarihinde saat {saat}'de {hizmet} randevunuz başarıyla oluşturuldu. Uzman: {calisan}. Teşekkürler!"
            
            # Tarihi güzel formatına çevir
            try:
                tarih_tr = datetime.strptime(tarih, "%Y-%m-%d").strftime("%d/%m/%Y")
            except Exception:
                tarih_tr = tarih
                
            # Şablonu doldur
            mesaj = sablon.format(
                ad=ad,
                tarih=tarih_tr,
                saat=saat,
                hizmet=hizmet_adi,
                calisan=calisan_adi,
                telefon=telefon
            )
            
            # İki farklı WhatsApp URL'i oluştur
            import urllib.parse
            encoded_mesaj = urllib.parse.quote(mesaj)
            
            # 1. wa.me URL'i (normal kullanıcı için)
            whatsapp_url = f"https://wa.me/{tel}?text={encoded_mesaj}"
            
            # 2. web.whatsapp.com URL'i (yeni sohbet başlatma için)
            whatsapp_web_url = f"https://web.whatsapp.com/send?phone={tel}&text={encoded_mesaj}"
            
            # Randevu bilgilerini session'a kaydet
            session['randevu_basarili'] = True
            session['whatsapp_url'] = whatsapp_url
            session['whatsapp_web_url'] = whatsapp_web_url
            session['randevu_mesaj'] = mesaj
            
            # Sunucudan WhatsApp mesajı gönder (arkaplanda)
            threading.Thread(target=whatsapp_mesaj_gonder, args=(tel, mesaj)).start()
            logger.info(f"WhatsApp mesajı gönderme işlemi başlatıldı: {tel}")
            
            
            flash("Randevunuz başarıyla oluşturuldu!", "success")
            return redirect(url_for('randevu_basarili'))

        return render_template('online_randevu.html', hizmetler=hizmetler, randevu_saatleri=randevu_saatleri, current_year=datetime.now().year)
    except Exception as e:
        logger.error(f"Online randevu hatası: {str(e)}")
        flash(f"Hata oluştu: {str(e)}", "danger")
        # Hata durumunda da otomatik oluşturulan saatleri kullan
        randevu_saatleri = generate_randevu_saatleri()
        return render_template('online_randevu.html', hizmetler=[], randevu_saatleri=randevu_saatleri, current_year=datetime.now().year)

# Online randevu için gerekli API
@app.route('/api/hizmet_sorumlu_calisanlar')
def hizmet_sorumlu_calisanlar():
    hizmet_id = request.args.get('hizmet_id')
    
    if not hizmet_id:
        return jsonify({'error': 'Hizmet ID parametresi gerekli'}), 400
        
    try:
        with closing(get_db_connection()) as conn:
            # Hizmetin sorumlu çalışanlarını bul
            hizmet_row = conn.execute("SELECT calisan_id FROM hizmetler WHERE id = ?", (hizmet_id,)).fetchone()
            
            if not hizmet_row or not hizmet_row['calisan_id']:
                return jsonify({'calisanlar': []})
            
            # Virgülle ayrılmış çalışan ID'lerini listeye çevir
            calisan_id_str = str(hizmet_row['calisan_id'])
            calisan_ids = [cid.strip() for cid in calisan_id_str.split(',') if cid.strip()]
            
            # Çalışan bilgilerini al
            calisanlar = []
            for cid in calisan_ids:
                calisan = conn.execute("SELECT id, ad FROM çalışanlar WHERE id = ?", (cid,)).fetchone()
                if calisan:
                    calisanlar.append({
                        'id': calisan['id'],
                        'ad': calisan['ad']
                    })
            
            return jsonify({'calisanlar': calisanlar})
    except Exception as e:
        logger.error(f"Hizmet çalışanları API hatası: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Online randevu için gerekli API
@app.route('/dolu_saatler')
def dolu_saatler():
    tarih = request.args.get('tarih')
    hizmet_id = request.args.get('hizmet_id')
    calisan_id = request.args.get('calisan_id')
    
    if not tarih:
        return jsonify({'error': 'Tarih parametresi gerekli'}), 400
        
    try:
        with closing(get_db_connection()) as conn:
            calisan_ids = []
            
            # Eğer hizmet_id varsa ve calisan_id yoksa, hizmetin sorumlu çalışanlarını bul
            if hizmet_id and not calisan_id:
                # Önce hizmetin calisan_id değerini kontrol et
                hizmet_row = conn.execute("SELECT calisan_id FROM hizmetler WHERE id = ?", (hizmet_id,)).fetchone()
                if hizmet_row and hizmet_row['calisan_id']:
                    # Virgülle ayrılmış çalışan ID'lerini listeye çevir
                    calisan_id_str = str(hizmet_row['calisan_id'])
                    calisan_ids = [cid.strip() for cid in calisan_id_str.split(',') if cid.strip()]
                    
                # Eğer calisan_id boşsa veya yoksa, ilişkisel tablodan çalışanları getir
                if not calisan_ids:
                    calisan_rows = conn.execute('''
                        SELECT c.id 
                        FROM çalışanlar c
                        JOIN calisan_hizmet ch ON c.id = ch.calisan_id
                        WHERE ch.hizmet_id = ?
                    ''', (hizmet_id,)).fetchall()
                    calisan_ids = [str(row['id']) for row in calisan_rows]
            elif calisan_id:
                # Eğer calisan_id doğrudan verilmişse, onu kullan
                calisan_ids = [calisan_id]
            
            # Hizmetin süresini al
            if hizmet_id:
                hizmet = conn.execute("SELECT seans FROM hizmetler WHERE id = ?", (hizmet_id,)).fetchone()
                seans_suresi = hizmet['seans'] if hizmet else 1
            else:
                seans_suresi = 1
                
            # Tüm çalışanların dolu saatlerini topla
            dolu_saatler = []
            dolu_calisanlar = {}
            
            if calisan_ids:
                # Her çalışan için dolu saatleri al
                for cid in calisan_ids:
                    query = "SELECT saat FROM randevular WHERE tarih = ? AND durum != 'İptal' AND çalışan_id = ?"
                    params = [tarih, cid]
                    dolu_randevular = conn.execute(query, params).fetchall()
                    calisan_dolu_saatler = [r['saat'] for r in dolu_randevular]
                    
                    # Genel dolu saatler listesine ekle
                    dolu_saatler.extend(calisan_dolu_saatler)
                    
                    # Her saat için hangi çalışanların dolu olduğunu kaydet
                    for saat in calisan_dolu_saatler:
                        if saat not in dolu_calisanlar:
                            dolu_calisanlar[saat] = []
                        dolu_calisanlar[saat].append(int(cid))
            else:
                # Çalışan belirtilmemişse tüm dolu saatleri al
                query = "SELECT saat, çalışan_id FROM randevular WHERE tarih = ? AND durum != 'İptal'"
                params = [tarih]
                dolu_randevular = conn.execute(query, params).fetchall()
                
                for r in dolu_randevular:
                    dolu_saatler.append(r['saat'])
                    
                    if r['saat'] not in dolu_calisanlar:
                        dolu_calisanlar[r['saat']] = []
                    
                    if r['çalışan_id']:
                        dolu_calisanlar[r['saat']].append(r['çalışan_id'])
            
            # Tekrar eden saatleri çıkar
            dolu_saatler = list(set(dolu_saatler))
            
            return jsonify({
                'dolu_saatler': dolu_saatler, 
                'calisan_ids': calisan_ids,
                'dolu_calisanlar': dolu_calisanlar
            })
    except Exception as e:
        logger.error(f"Dolu saatler API hatası: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Bu sayfa da dış erişime açık
@app.route('/randevu_basarili')
def randevu_basarili():
    # Session'dan bilgileri al
    whatsapp_url = session.get('whatsapp_url')
    randevu_mesaj = session.get('randevu_mesaj')
    
    if not session.get('randevu_basarili'):
        return redirect(url_for('online_randevu'))
    
    # Session'dan bilgileri temizle (whatsapp_url ve randevu_mesaj hariç)
    session.pop('randevu_basarili', None)
    
    return render_template('randevu_basarili.html', 
                          whatsapp_url=whatsapp_url, 
                          randevu_mesaj=randevu_mesaj,
                          current_year=datetime.now().year)

# Güncelleme kontrolü için API
@app.route('/guncelleme_kontrol')
@login_required
def guncelleme_kontrol():
    if current_user.rol != 'admin':
        return jsonify({'success': False, 'error': 'Yetkiniz yok!'})
    
    try:
        from guncelleme import surum_kontrol
        guncelleme_bilgisi = surum_kontrol()
        
        if guncelleme_bilgisi:
            return jsonify({
                'success': True, 
                'guncelleme_var': True,
                'yeni_surum': guncelleme_bilgisi.get('yeni_surum'),
                'aciklama': guncelleme_bilgisi.get('aciklama')
            })
        else:
            return jsonify({'success': True, 'guncelleme_var': False})
    except Exception as e:
        logger.error(f"Güncelleme kontrolü sırasında hata: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/guncelleme_indir')
@login_required
def guncelleme_indir():
    if current_user.rol != 'admin':
        return jsonify({'success': False, 'error': 'Yetkiniz yok!'})
    
    try:
        from guncelleme import surum_kontrol, guncelleme_indir
        guncelleme_bilgisi = surum_kontrol()
        
        if guncelleme_bilgisi:
            # Güncelleme işlemini arkaplanda başlat
            threading.Thread(target=guncelleme_indir, args=(guncelleme_bilgisi,)).start()
            return jsonify({'success': True, 'message': 'Güncelleme başlatıldı'})
        else:
            return jsonify({'success': False, 'error': 'Güncelleme bulunamadı'})
    except Exception as e:
        logger.error(f"Güncelleme indirme sırasında hata: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

# Otomatik güncelleme kontrolü için arkaplan görevi
def otomatik_guncelleme_kontrol_gorevi():
    while True:
        try:
            # Haftada bir güncelleme kontrolü yap
            time.sleep(7 * 24 * 60 * 60)  # 7 gün
            
            from guncelleme import otomatik_guncelleme_kontrol
            otomatik_guncelleme_kontrol()
        except Exception as e:
            logger.error(f"Otomatik güncelleme kontrolü sırasında hata: {str(e)}")

if __name__ == '__main__':
    init_db()  # Veritabanı şemasını güncelle
    update_payment_status()
    
    # Otomatik güncelleme kontrolü için arkaplan görevi başlat
    guncelleme_thread = threading.Thread(target=otomatik_guncelleme_kontrol_gorevi, daemon=True)
    guncelleme_thread.start()
    
    app.run(host="0.0.0.0", port=5000, debug=False)  # Canlı ortamda debug=False olmalı