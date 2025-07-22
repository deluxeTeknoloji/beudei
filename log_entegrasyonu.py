"""
Bu dosya, fidel.py'ye eklenecek log sistemi kodlarını içerir.
Aşağıdaki adımları izleyin:

1. fidel.py dosyasının başına ekleyin:
```python
from functools import wraps
```

2. init_db fonksiyonuna log tablosu oluşturma kodunu ekleyin:
```python
# Log tablosu
conn.execute('''
    CREATE TABLE IF NOT EXISTS log_kayitlari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tarih TEXT NOT NULL,
        kullanici_id INTEGER,
        kullanici_adi TEXT,
        islem_turu TEXT NOT NULL,
        aciklama TEXT NOT NULL,
        ip_adresi TEXT,
        FOREIGN KEY (kullanici_id) REFERENCES kullanicilar(id)
    )
''')
```

3. Aşağıdaki fonksiyonları fidel.py dosyasına ekleyin:
"""

def log_ekle(conn, islem_turu, aciklama, kullanici_id=None, kullanici_adi=None, ip_adresi=None):
    """Sisteme log kaydı ekler"""
    from datetime import datetime
    
    tarih = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    conn.execute('''
        INSERT INTO log_kayitlari (tarih, kullanici_id, kullanici_adi, islem_turu, aciklama, ip_adresi)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (tarih, kullanici_id, kullanici_adi, islem_turu, aciklama, ip_adresi))
    conn.commit()

def log_action(islem_turu):
    """Log kaydı tutmak için decorator"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            result = f(*args, **kwargs)
            
            try:
                # İşlem sonrası log kaydı ekle
                with closing(get_db_connection()) as conn:
                    kullanici_id = current_user.id if current_user.is_authenticated else None
                    kullanici_adi = current_user.kullanici_adi if current_user.is_authenticated else None
                    ip_adresi = request.remote_addr
                    
                    # İşlem açıklamasını oluştur
                    aciklama = f"{f.__name__} fonksiyonu çalıştırıldı"
                    
                    # Özel işlem türleri için açıklama ekle
                    if 'musteri_id' in kwargs:
                        aciklama += f" - Müşteri ID: {kwargs['musteri_id']}"
                    elif 'id' in kwargs:
                        aciklama += f" - ID: {kwargs['id']}"
                    
                    log_ekle(conn, islem_turu, aciklama, kullanici_id, kullanici_adi, ip_adresi)
            except Exception as e:
                logger.error(f"Log kaydı eklenirken hata: {str(e)}")
                
            return result
        return decorated_function
    return decorator

@app.route('/log_kayitlari')
@login_required
def log_kayitlari():
    if current_user.rol != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!', 'danger')
        return redirect(url_for('index'))
    
    try:
        # Filtreleme parametreleri
        islem_turu = request.args.get('islem_turu')
        baslangic_tarihi = request.args.get('baslangic_tarihi')
        bitis_tarihi = request.args.get('bitis_tarihi')
        kullanici = request.args.get('kullanici')
        
        with closing(get_db_connection()) as conn:
            # Sorgu oluştur
            query = "SELECT * FROM log_kayitlari"
            params = []
            where_clauses = []
            
            if islem_turu:
                where_clauses.append("islem_turu = ?")
                params.append(islem_turu)
                
            if baslangic_tarihi:
                where_clauses.append("tarih >= ?")
                params.append(baslangic_tarihi + " 00:00:00")
                
            if bitis_tarihi:
                where_clauses.append("tarih <= ?")
                params.append(bitis_tarihi + " 23:59:59")
                
            if kullanici:
                where_clauses.append("kullanici_adi LIKE ?")
                params.append(f"%{kullanici}%")
                
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
                
            query += " ORDER BY tarih DESC LIMIT 1000"
            
            loglar = conn.execute(query, params).fetchall()
            
            # İşlem türlerini al (filtreleme için)
            islem_turleri = conn.execute("SELECT DISTINCT islem_turu FROM log_kayitlari").fetchall()
            
            # Kullanıcıları al (filtreleme için)
            kullanicilar = conn.execute("SELECT DISTINCT kullanici_adi FROM log_kayitlari WHERE kullanici_adi IS NOT NULL").fetchall()
            
        return render_template('log_kayitlari.html', 
                              loglar=loglar, 
                              islem_turleri=islem_turleri,
                              kullanicilar=kullanicilar,
                              filtreler={
                                  'islem_turu': islem_turu,
                                  'baslangic_tarihi': baslangic_tarihi,
                                  'bitis_tarihi': bitis_tarihi,
                                  'kullanici': kullanici
                              })
    except Exception as e:
        logger.error(f"Log kayıtları sayfası hatası: {str(e)}")
        flash(f'Log kayıtları alınırken hata oluştu: {str(e)}', 'danger')
        return redirect(url_for('index'))

# Örnek log kullanımı:
"""
@app.route('/musteri_sil/<int:musteri_id>', methods=['POST'])
@login_required
@log_action('Müşteri Silme')  # Bu decorator ile otomatik log kaydı oluşturulur
def musteri_sil(musteri_id):
    try:
        with closing(get_db_connection()) as conn:
            with conn:
                # Müşteri bilgilerini al (log için)
                musteri = conn.execute('SELECT ad FROM müşteriler WHERE id = ?', (musteri_id,)).fetchone()
                musteri_adi = musteri['ad'] if musteri else "Bilinmeyen Müşteri"
                
                # Müşteriyi sil
                conn.execute('DELETE FROM müşteriler WHERE id = ?', (musteri_id,))
                
                # Manuel log kaydı ekle (daha detaylı bilgi için)
                log_ekle(
                    conn, 
                    'Müşteri Silme', 
                    f"{musteri_adi} isimli müşteri silindi", 
                    current_user.id, 
                    current_user.kullanici_adi, 
                    request.remote_addr
                )
                
        flash("Müşteri başarıyla silindi.", "success")
    except Exception as e:
        logger.error(f"Müşteri silme hatası: {str(e)}")
        flash(f"Müşteri silinirken hata oluştu: {str(e)}", "danger")
    return redirect(url_for('musteri_listesi'))
"""