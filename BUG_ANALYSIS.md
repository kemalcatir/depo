# Nöbet Sistemi - Hata Analizi

## 🔴 KRİTİK HATALAR

### 1. **Priority Score Underflow - ÇOK ÖNEMLİ**
```python
priority = 10000  # ❌ YETERSİZ!

# Olası ceza toplamı:
# - Kural 8: -999,999
# - Adil Dağılım: -2,000 * 20 personel = -40,000
# - Perşembe/Cuma: -3,000 * 20 = -60,000
# - Haftasonu: -4,000 * 20 = -80,000
# - Prev Month Score: -1,500 * 20 = -30,000
# TOPLAM = -1,209,999 ❌
```

**Etki**: Negatif priority değerleri sorting'i bozar, yanlış kişiler seçilir.

**ÇÖZÜM**:
```python
priority = 100000000  # Yeterince büyük başla
```

---

### 2. **Boş Roster Durumu - KRİTİK**
```python
selected = candidates[:num_per_day]
selected_ids = [c[1] for c in selected]
```

Eğer `candidates` boşsa (tüm personel kısıtlanmışsa) → `selected_ids` boş → roster'a boş isim listesi eklenir.

**ÇÖZÜM**:
```python
if not candidates:
    logging.warning(f"Tarih {date_str}: Uygun personel bulunamadı!")
    # Fallback: En az kalan personeli seç (hard constraint'i gevşet)
    fallback_candidates = [(0, p_id) for p_id in personnel_ids]
    selected = fallback_candidates[:num_per_day]
    selected_ids = [c[1] for c in selected]

if len(selected_ids) < num_per_day:
    logging.warning(f"Tarih {date_str}: Sadece {len(selected_ids)}/{num_per_day} personel atandı")
```

---

### 3. **Tarih Format Hatası - ÖNEMLİ**
```python
tarih_dt = datetime.strptime(tarih, "%Y-%m-%d")
```

Eğer database'deki `tarih` tipi **INTEGER** (Unix timestamp) veya **başka format** ise → `ValueError`

**ÇÖZÜM**:
```python
def parse_date(tarih):
    """Çeşitli format desteği"""
    if isinstance(tarih, str):
        for fmt in ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"]:
            try:
                return datetime.strptime(tarih, fmt)
            except ValueError:
                continue
        raise ValueError(f"Geçersiz tarih formatı: {tarih}")
    elif isinstance(tarih, datetime):
        return tarih
    else:
        raise ValueError(f"Bilinmeyen tarih tipi: {type(tarih)}")

# Kullanım:
tarih_dt = parse_date(tarih)
```

---

### 4. **None İçin Karşılaştırma Hatası**
```python
if last_duty and (current_date - last_duty).days < MIN_REST_DAYS:
```

`last_duty = None` ise geçer (doğru), ama eğer `last_duty` datetime değilse → TypeError

**ÇÖZÜM**:
```python
if last_duty and isinstance(last_duty, datetime):
    if (current_date - last_duty).days < MIN_REST_DAYS:
        continue
```

---

### 5. **Önceki Ay Verisi Hatasız Alınmıyor - ÖNEMLİ**
```python
cursor.execute("SELECT personel_id, tarih FROM aylik_nobetler WHERE ...", 
               (str(prev_y), f"{prev_m:02d}"))
```

Ocak ayının öncesi (Aralık) alınırken, **yıl geçişi yanlış** olabilir:
- `datetime(2025, 1, 1) - timedelta(days=1)` = `2024-12-31` ✅ (Doğru)

Ama eğer format kontrolü yoksa sorun.

---

## 🟡 UYARI SEVİYESİ HATALAR

### 6. **Database Bağlantı Kontrol Eksikliği**
```python
cursor.execute("""SELECT ...WHERE strftime('%m', tarih) = ?""", (f"{month:02d}",))
```

`strftime()` SQLite'a özel! Başka veritabanı sisteminde çalışmaz.

---

### 7. **Exception Handling Çok Gevşek**
```python
except sqlite3.OperationalError:
    rows = []
```

Tüm hataları susturuyor (silent fail). Logging ekle:
```python
except sqlite3.OperationalError as e:
    logging.error(f"Database hatası: {e}")
    rows = []
```

---

### 8. **Stats Tahminlemesi Eksik**
Eğer `prev_month_data` da `total_count` yoksa:
```python
prev_month_score = prev_month_data.get(p_id, {}).get('total_count', 0)
```

Aman doğru (default 0), ama güvenilir değil.

---

## 📋 ÖNERİLEN FİKS PRİYORİTESİ

| Hata | Ciddiyeti | Etkisi |
|------|-----------|--------|
| Priority underflow | 🔴 KRİTİK | Yanlış seçimler |
| Boş roster | 🔴 KRİTİK | İş atlaması |
| Tarih formatı | 🟠 ÖNEMLİ | Crash |
| Database exception | 🟡 UYARI | Silent failures |

---

## ✅ FIXED VERSION

Aşağıdaki dosyada düzeltilmiş kod yer alır: `generate_plan_fixed.py`
