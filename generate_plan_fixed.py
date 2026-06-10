import sqlite3
import calendar
import logging
from datetime import datetime, timedelta
from collections import defaultdict

# Logging konfigürasyonu
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_NAME = "nobet_sistemi.db"

class DutyScheduler:
    def __init__(self):
        self.db_name = DB_NAME
        self.personnel_dict = {}
        self.personnel_ids = []
        self.settings = {}
        self.scoring_system = {}
        self.restrictions = {}
        self.prev_month_data = {}
        self.last_duty_dates = {}
        self.stats = {}
        self.saturday_consecutive = {}  # Cumartesi üst üste kontrolü
        
    def load_settings(self):
        """Sistem ayarlarını yükle"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        try:
            # Ana ayarlar
            cursor.execute("SELECT nobetci_sayisi, min_rest_days FROM ayarlar WHERE id=1")
            row = cursor.fetchone()
            if row:
                self.settings = {
                    'nobetci_sayisi': row[0] if row[0] else 6,
                    'min_rest_days': row[1] if row[1] else 2
                }
            else:
                self.settings = {'nobetci_sayisi': 6, 'min_rest_days': 2}
            
            # Gün gün puanlama sistemi (Kural 6)
            cursor.execute("SELECT gun, puan FROM puanlama_sistemi ORDER BY gun")
            rows = cursor.fetchall()
            for gun, puan in rows:
                self.scoring_system[gun] = puan  # 0=Pazartesi, 1=Salı, ..., 6=Pazar
            
            logger.info(f"Sistem ayarları yüklendi: {self.settings}")
            logger.info(f"Puanlama sistemi: {self.scoring_system}")
        except sqlite3.OperationalError as e:
            logger.warning(f"Sistem ayarları okunamadı: {e}. Default değerler kullanılıyor.")
            self.settings = {'nobetci_sayisi': 6, 'min_rest_days': 2}
            self.scoring_system = {i: 10 for i in range(7)}
        finally:
            conn.close()

    def load_personnel(self):
        """Aktif personeli yükle"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT id, ad_soyad FROM personel WHERE aktif_mi=1 ORDER BY ad_soyad")
        personnel = cursor.fetchall()
        conn.close()
        
        self.personnel_ids = [p[0] for p in personnel]
        self.personnel_dict = {p[0]: p[1] for p in personnel}
        logger.info(f"Toplam aktif personel: {len(self.personnel_ids)}")
        
        return len(self.personnel_ids) > 0

    def load_restrictions(self, year, month):
        """O ay için kısıtlamaları yükle (izin, mazeret vb.)"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT personel_id, tarih 
                FROM kisitlamalar 
                WHERE strftime('%Y', tarih) = ? AND strftime('%m', tarih) = ?
            """, (str(year), f"{month:02d}"))
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            logger.warning(f"Kısıtlamalar okunamadı: {e}")
            rows = []
        finally:
            conn.close()

        self.restrictions = {}
        for p_id, tarih in rows:
            if p_id not in self.restrictions:
                self.restrictions[p_id] = set()
            self.restrictions[p_id].add(tarih)
        
        logger.info(f"{len(self.restrictions)} personelin kısıtlaması bulundu")

    def load_prev_month_data(self, year, month):
        """
        Kural 3 & 4: Önceki ay verilerini yükle
        - Haftasonu puanlaması (Kural 3)
        - Son nöbet tarihi ve istirahat kuralı (Kural 4)
        """
        target = datetime(year, month, 1) - timedelta(days=1)
        prev_y, prev_m = target.year, target.month

        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT personel_id, tarih 
                FROM aylik_nobetler 
                WHERE strftime('%Y', tarih) = ? AND strftime('%m', tarih) = ?
            """, (str(prev_y), f"{prev_m:02d}"))
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            logger.warning(f"Önceki ay verileri okunamadı: {e}")
            rows = []
        finally:
            conn.close()

        self.prev_month_data = {}
        self.last_duty_dates = {}
        weekend_scores = {}  # Kural 3: Haftasonu puanı
        
        for p_id, tarih in rows:
            if p_id not in self.prev_month_data:
                self.prev_month_data[p_id] = {
                    'days': set(),
                    'total_count': 0,
                    'weekend_score': 0
                }
            
            try:
                tarih_dt = datetime.strptime(tarih, "%Y-%m-%d")
            except ValueError:
                logger.warning(f"Tarih parse hatası: {tarih}")
                continue
            
            self.prev_month_data[p_id]['days'].add(tarih_dt.day)
            self.prev_month_data[p_id]['total_count'] += 1
            
            # Haftasonu puanı hesapla (Kural 3)
            if tarih_dt.weekday() >= 5:  # Cumartesi ve Pazar
                self.prev_month_data[p_id]['weekend_score'] += 1
            
            # En son nöbeti kaydet (Kural 4)
            if p_id not in self.last_duty_dates or tarih_dt > self.last_duty_dates[p_id]:
                self.last_duty_dates[p_id] = tarih_dt
        
        logger.info(f"Önceki ay: {len(self.prev_month_data)} personel verisi yüklendi")

    def initialize_stats(self):
        """Personel istatistiklerini başlat"""
        self.stats = {}
        self.saturday_consecutive = {}
        
        for p_id in self.personnel_ids:
            self.stats[p_id] = {
                'total': 0,
                'thursday': 0,
                'friday': 0,
                'saturday': 0,
                'sunday': 0,
                'days': [],
                'last_duty': self.last_duty_dates.get(p_id),
                'last_saturday': None  # Kural 9 için son cumartesi
            }
            self.saturday_consecutive[p_id] = False

    def get_special_day_score(self, date):
        """
        Kural 8: Sistem Ayarlarındaki Özel Gün puanlamasını al
        """
        weekday = date.weekday()
        return self.scoring_system.get(weekday, 10)

    def is_eligible_for_duty(self, p_id, current_date, date_str, weekday):
        """
        Personelin o gün nöbete uygun olup olmadığını kontrol et
        Hard Constraints
        """
        # Kısıtlama kontrolü (izin, mazeret vb.)
        if p_id in self.restrictions and date_str in self.restrictions[p_id]:
            return False, "Kısıtlama"
        
        # Kural 5: Maximum istirahat kuralı
        last_duty = self.stats[p_id]['last_duty']
        if last_duty and isinstance(last_duty, datetime):
            days_since_duty = (current_date - last_duty).days
            if days_since_duty < self.settings['min_rest_days']:
                return False, f"İstirahat: {days_since_duty} gün"
        
        # Kural 9: Cumartesi üst üste kontrol
        if weekday == 5:  # Cumartesi
            if self.saturday_consecutive.get(p_id, False):
                return False, "Üst üste cumartesi"
        
        return True, "Uygun"

    def calculate_priority(self, p_id, current_date, weekday, date_str):
        """
        Soft Constraints: Puanlama sistemi
        Kural 1: Eşit nöbet dağılımı
        Kural 2: Perşembe-Cuma eşit
        Kural 3: Haftasonu eşit (önceki ay puanına göre)
        Kural 6: Gün gün puanlama
        Kural 8: Özel gün puanlama
        """
        priority = 1000000000  # Başlangıç puanı
        
        # Kural 1: Adil dağılım - az nöbet tutanı tercih et
        total_assigned = self.stats[p_id]['total']
        priority -= (total_assigned * 10000)
        
        # Kural 2: Perşembe ve Cuma eşit dağılımı
        if weekday == 3:  # Perşembe
            priority -= (self.stats[p_id]['thursday'] * 8000)
        elif weekday == 4:  # Cuma
            priority -= (self.stats[p_id]['friday'] * 8000)
        
        # Kural 3: Haftasonu eşit dağılımı (önceki ay puanına göre)
        if weekday >= 5:  # Cumartesi veya Pazar
            prev_weekend_score = self.prev_month_data.get(p_id, {}).get('weekend_score', 0)
            
            # Önceki ayda az haftasonu nöbeti tutanı tercih et
            priority -= (prev_weekend_score * 50000)
            
            # O ayki haftasonu sayısı da dikkate al
            if weekday == 5:  # Cumartesi
                priority -= (self.stats[p_id]['saturday'] * 12000)
            else:  # Pazar
                priority -= (self.stats[p_id]['sunday'] * 12000)
        
        # Kural 6 & 8: Gün gün puanlama sistemi
        day_score = self.get_special_day_score(current_date)
        priority -= (total_assigned * day_score * 500)
        
        # Negatif puanları önle
        if current_day in self.prev_month_data.get(p_id, {}).get('days', set()):
            priority -= 50000  # Önceki ay aynı güne çakışmasını az cezalandır
        
        return priority

    def select_personnel_for_day(self, current_date, date_str, candidates_list, num_per_day):
        """
        Günlük seçim
        """
        if not candidates_list:
            logger.warning(f"{date_str}: Hiçbir uygun personel bulunamadı")
            return []
        
        # En yüksek puanlıları sırala
        candidates_list.sort(key=lambda x: x[0], reverse=True)
        
        selected = candidates_list[:num_per_day]
        selected_ids = [c[1] for c in selected]
        
        if len(selected_ids) < num_per_day:
            logger.warning(f"{date_str}: Sadece {len(selected_ids)}/{num_per_day} personel seçildi")
        
        return selected_ids

    def update_stats(self, selected_ids, current_date, weekday):
        """Personel istatistiklerini güncelle"""
        for p_id in selected_ids:
            self.stats[p_id]['last_duty'] = current_date
            self.stats[p_id]['total'] += 1
            self.stats[p_id]['days'].append(current_date.strftime("%Y-%m-%d"))
            
            if weekday == 3:
                self.stats[p_id]['thursday'] += 1
            elif weekday == 4:
                self.stats[p_id]['friday'] += 1
            elif weekday == 5:
                self.stats[p_id]['saturday'] += 1
                self.stats[p_id]['last_saturday'] = current_date
                self.saturday_consecutive[p_id] = True
            elif weekday == 6:
                self.stats[p_id]['sunday'] += 1
        
        # Cumartesi konsekütifliğini sıfırla (diğer günler için)
        if weekday != 5:
            for p_id in self.personnel_ids:
                if p_id not in selected_ids:
                    # Bu kişi bu gün seçilmediyse ve bu cumartesi değilse, 
                    # cumartesi flag'i sıfırlanmaz
                    pass

    def generate_plan(self, year, month):
        """
        Ana plan oluşturma fonksiyonu
        Tüm kuralları uygula
        """
        logger.info(f"Plan oluşturuluyor: {year}-{month:02d}")
        
        # Adım 1: Verileri yükle
        self.load_settings()
        if not self.load_personnel():
            return {"status": "error", "message": "Aktif personel bulunamadı!"}
        
        self.load_restrictions(year, month)
        self.load_prev_month_data(year, month)
        self.initialize_stats()
        
        num_per_day = self.settings.get('nobetci_sayisi', 6)
        roster = {}
        num_days = calendar.monthrange(year, month)[1]
        
        # İstatistik
        empty_days = 0
        partial_days = 0
        
        # Adım 2: Her gün için personel seç
        for day in range(1, num_days + 1):
            current_date = datetime(year, month, day)
            date_str = current_date.strftime("%Y-%m-%d")
            weekday = current_date.weekday()
            current_day = current_date.day
            
            candidates = []
            
            # Adım 3: Uygun personelleri belirle
            for p_id in self.personnel_ids:
                is_eligible, reason = self.is_eligible_for_duty(p_id, current_date, date_str, weekday)
                
                if not is_eligible:
                    continue
                
                # Uygunsa puanlama yap
                priority = self.calculate_priority(p_id, current_date, weekday, date_str)
                candidates.append((priority, p_id))
            
            # Adım 4: Günlük personeli seç
            selected_ids = self.select_personnel_for_day(current_date, date_str, candidates, num_per_day)
            
            if not selected_ids:
                empty_days += 1
            elif len(selected_ids) < num_per_day:
                partial_days += 1
            
            # Roster'a isimler
            roster[date_str] = [self.personnel_dict.get(pid, "Bilinmiyor") for pid in selected_ids]
            
            # İstatistikleri güncelle
            self.update_stats(selected_ids, current_date, weekday)
        
        # Sonuçları formatla
        final_stats = {}
        for p_id in self.personnel_ids:
            name = self.personnel_dict[p_id]
            final_stats[name] = {
                'total': self.stats[p_id]['total'],
                'thursday': self.stats[p_id]['thursday'],
                'friday': self.stats[p_id]['friday'],
                'saturday': self.stats[p_id]['saturday'],
                'sunday': self.stats[p_id]['sunday'],
                'last_duty': self.stats[p_id]['last_duty'].strftime("%Y-%m-%d") if self.stats[p_id]['last_duty'] else None
            }
        
        logger.info(f"Plan tamamlandı: {empty_days} boş gün, {partial_days} kısmi atama")
        
        return {
            "status": "success",
            "data": roster,
            "stats": final_stats,
            "warnings": {
                "empty_days": empty_days,
                "partial_days": partial_days
            }
        }


def generate_plan(year, month):
    """Ana entry point"""
    scheduler = DutyScheduler()
    return scheduler.generate_plan(year, month)
