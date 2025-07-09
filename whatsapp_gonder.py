from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def whatsapp_mesaj_gonder(telefon, mesaj):
    import os, time, traceback
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
    import urllib.parse

    try:
        # Mesajı URL-encode yap
        encoded_mesaj = urllib.parse.quote(mesaj)
        
        # Doğrudan WhatsApp API URL'ini kullan
        whatsapp_url = f"https://web.whatsapp.com/send?phone={telefon}&text={encoded_mesaj}"
        
        profile_path = os.path.join(os.path.expanduser("~"), "whatsapp_profile")
        options = webdriver.ChromeOptions()
        options.add_argument(f"user-data-dir={profile_path}")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.get(whatsapp_url)
        driver.set_window_size(800, 600)  # Biraz daha büyük pencere
        print("Lütfen ilk seferde QR kodunu okutun. Sonraki çalıştırmalarda gerek kalmayacak.")

        # Mesaj gönderme butonunu bekle
        wait = WebDriverWait(driver, 60)
        try:
            # Mesaj kutusunun yüklenmesini bekle
            mesaj_kutusu = wait.until(
                EC.presence_of_element_located((By.XPATH, "//div[@contenteditable='true'][@data-tab='10']"))
            )
            
            # Enter tuşuna basarak mesajı gönder
            mesaj_kutusu.send_keys(Keys.ENTER)
            print("Mesaj gönderildi!")
            time.sleep(5)
        except Exception as inner_e:
            print(f"Mesaj gönderme hatası: {str(inner_e)}")
            # Hata durumunda ekran görüntüsü al
            driver.save_screenshot("whatsapp_error.png")
            
        driver.quit()
    except Exception as e:
        with open("whatsapp_hata.log", "a", encoding="utf-8") as f:
            f.write(traceback.format_exc())