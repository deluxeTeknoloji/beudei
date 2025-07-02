from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import time
import os

def whatsapp_mesaj_gonder(telefon, mesaj):
    # Chrome profil klasörü (örnek: C:\Users\KULLANICI_ADI\whatsapp_profile)
    profile_path = os.path.join(os.path.expanduser("~"), "whatsapp_profile")
    options = webdriver.ChromeOptions()
    options.add_argument(f"user-data-dir={profile_path}")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.get("https://web.whatsapp.com/")
    driver.set_window_size(200, 400)  # Pencereyi küçült
    print("Lütfen ilk seferde QR kodunu okutun. Sonraki çalıştırmalarda gerek kalmayacak.")
    time.sleep(10)  # Giriş için bekleme

    # Arama kutusunu bul ve numarayı yaz
    success = False
    for _ in range(20):
        try:
            arama_kutusu = driver.find_element(By.XPATH, "//div[@contenteditable='true'][@data-tab='3']")
            arama_kutusu.click()
            arama_kutusu.clear()
            arama_kutusu.send_keys(telefon)
            time.sleep(2)
            arama_kutusu.send_keys(Keys.ENTER)
            success = True
            break
        except Exception:
            time.sleep(1)

    if not success:
        print("Arama kutusu bulunamadı veya sohbet açılamadı.")
        driver.quit()
        return

    # Mesaj kutusunu bul ve mesajı gönder
    success = False
    for _ in range(20):
        try:
            mesaj_kutusu = driver.find_element(By.XPATH, "//div[@contenteditable='true'][@data-tab='10']")
            mesaj_kutusu.click()
            mesaj_kutusu.send_keys(mesaj)
            mesaj_kutusu.send_keys(Keys.ENTER)
            print("Mesaj gönderildi!")
            success = True
            break
        except Exception:
            time.sleep(1)
    if not success:
        print("Mesaj kutusu bulunamadı, manuel onay gerekebilir.")

    time.sleep(5)
    driver.quit()

if __name__ == "__main__":
    telefon = "905469250500"  # Numara başında 90 ile
    mesaj = "Test mesajı: WhatsApp otomasyon başarılı!"
    whatsapp_mesaj_gonder(telefon, mesaj)