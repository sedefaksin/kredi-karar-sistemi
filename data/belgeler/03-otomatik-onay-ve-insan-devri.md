# Otomatik Onay, Red ve İnsan Devri Politikası

## Madde 1 — Otomatik Onay Koşulları
Bir başvurunun sistem tarafından ONAY ÖNERİSİ alabilmesi için AŞAĞIDAKİLERİN TÜMÜ
sağlanmalı ve karar girdileri doğrulanmış olmalıdır. Bu öneri nihai kredi kararı değildir;
her başvuruda son karar yetkili bir kredi uzmanı tarafından verilir:
- Risk skoru 25'in altında olmalı
- Borç/gelir oranı %50'nin altında olmalı
- Son 24 ayda temerrüt kaydı olmamalı
- Kredi geçmişi en az 12 ay olmalı

## Madde 2 — Temerrüt Geçmişi ve İnsan Devri
Son 24 ay içinde bir veya daha fazla temerrüt (ödeme gecikmesi) kaydı olan
başvuru sahipleri, talep edilen tutar ne olursa olsun insan onayı sürecine
yönlendirilir; sistem otomatik onay veya nihai ret veremez.

## Madde 3 — Otomatik Red Koşulları
Risk skoru 75'in üzerinde olan VE aynı zamanda son 24 ayda temerrüt kaydı
bulunan başvurular için sistem ret önerisi üretir. Nihai ret kararı yetkili
kredi uzmanı tarafından verilir. Başvuru sahibi ret kararına 15 gün içinde
şubeden itiraz edebilir.

## Madde 4 — İnsan Onayı Süreci
Uzman incelemesi gereken başvurular ilgili şubedeki kredi uzmanına atanır.
Uzman, sistemin ürettiği risk raporunu ve dayandığı politika maddelerini
inceleyerek 5 iş günü içinde nihai kararı verir. Karar, başvuru sahibine
SMS ve e-posta ile bildirilir.

## Madde 5 — Denetim ve Kayıt Tutma
Her değerlendirme önerisi ve nihai uzman kararı denetim kaydına yazılır.
Bu bitirme projesi yerel SQLite günlüğü kullanır; mevzuata uygun saklama
süresi, yedekleme ve değiştirilemez denetim altyapısı sağlamaz.
