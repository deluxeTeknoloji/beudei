import sqlite3

conn = sqlite3.connect('kuaför.db')
conn.row_factory = sqlite3.Row

# Engin Utku müşterisinin detaylarını kontrol et
musteri = conn.execute("SELECT * FROM müşteriler WHERE ad LIKE '%Engin%'").fetchone()
if musteri:
    print(f"Müşteri: {musteri['ad']}")
    print(f"Bakiye: {musteri['bakiye']}")
    print(f"ID: {musteri['id']}")
    
    # Bu müşterinin satışlarını kontrol et
    satislar = conn.execute("SELECT * FROM satislar WHERE musteri_id = ?", (musteri['id'],)).fetchall()
    print(f"\nSatış sayısı: {len(satislar)}")
    toplam_borc = 0
    for s in satislar:
        print(f"Satış ID: {s['id']}, Fiyat: {s['fiyat']}, Açıklama: {s['aciklama']}")
        toplam_borc += s['fiyat']
    
    print(f"\nToplam borç olması gereken: -{toplam_borc}")
    print(f"Mevcut bakiye: {musteri['bakiye']}")
    
    # Bakiyeyi manuel güncelle
    yeni_bakiye = -toplam_borc
    conn.execute("UPDATE müşteriler SET bakiye = ? WHERE id = ?", (yeni_bakiye, musteri['id']))
    conn.commit()
    print(f"Bakiye güncellendi: {yeni_bakiye}")

conn.close()