from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def whatsapp_mesaj_gonder(telefon, mesaj):
    import os, time, traceback
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service

    try:
        profile_path = os.path.join(os.path.expanduser("~"), "whatsapp_profile")
        options = webdriver.ChromeOptions()
        options.add_argument(f"user-data-dir={profile_path}")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.get("https://web.whatsapp.com/")
        driver.set_window_size(200, 400)
        print("Lütfen ilk seferde QR kodunu okutun. Sonraki çalıştırmalarda gerek kalmayacak.")

        wait = WebDriverWait(driver, 60)
        # Arama kutusunu bekle
        try:
            arama_kutusu = wait.until(
                EC.presence_of_element_located((By.XPATH, "//div[@contenteditable='true'][@data-tab='3']"))
            )
        except:
            # Alternatif XPATH dene
            arama_kutusu = wait.until(
                EC.presence_of_element_located((By.XPATH, "//div[@title='Ara veya yeni sohbet başlat']"))
            )

        arama_kutusu.click()
        arama_kutusu.clear()
        arama_kutusu.send_keys(telefon)
        time.sleep(2)
        arama_kutusu.send_keys(Keys.ENTER)
        time.sleep(2)

        # Mesaj kutusunu bekle
        mesaj_kutusu = wait.until(
            EC.presence_of_element_located((By.XPATH, "//div[@contenteditable='true'][@data-tab='10']"))
        )
        mesaj_kutusu.click()
        mesaj_kutusu.send_keys(mesaj)
        mesaj_kutusu.send_keys(Keys.ENTER)
        print("Mesaj gönderildi!")
        time.sleep(5)
        driver.quit()
    except Exception as e:
        with open("whatsapp_hata.log", "a", encoding="utf-8") as f:
            f.write(traceback.format_exc())