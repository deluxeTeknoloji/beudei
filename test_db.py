import sqlite3

# Veritabanı bağlantısı
conn = sqlite3.connect('kuaför.db')
conn.row_factory = sqlite3.Row

# Hizmetleri kontrol et
print("=== HİZMETLER ===")
hizmetler = conn.execute('SELECT * FROM hizmetler').fetchall()
for h in hizmetler:
    print(f"ID: {h['id']}, Ad: {h['hizmet_adi']}, Fiyat: {h['fiyat']}")

print("\n=== ONLINE RANDEVU SATIŞLARI ===")
satislar = conn.execute("""
    SELECT s.*, h.hizmet_adi, m.ad as musteri_ad, m.bakiye 
    FROM satislar s 
    LEFT JOIN hizmetler h ON s.urun_id = h.id 
    LEFT JOIN müşteriler m ON s.musteri_id = m.id 
    WHERE s.aciklama LIKE '%online%'
""").fetchall()

for s in satislar:
    print(f"Müşteri: {s['musteri_ad']}, Hizmet: {s['hizmet_adi']}, Fiyat: {s['fiyat']}, Bakiye: {s['bakiye']}")

conn.close()