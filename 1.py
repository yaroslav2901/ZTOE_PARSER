TZ = ZoneInfo("Europe/Kyiv")
today = datetime.now(TZ).date()
lastUpdated = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
==========================================================

{
  "regionId": "Zhytomyr",
  "lastUpdated": "2025-12-08T06:54:55.423Z", <-- Дата та час останнього парсингу сайту  lastUpdated (UTC, формат ISO8601)
  "fact": {
    "data": {
      "1765144800": { <--Ключ — Unix timestamp (Europe/Kyiv), дата графіку відключень
        "GPV1.1": {
            //...
        },
        //...
        "GPV6.2": {
            //...
        }
      }
    },
    "update": "08.12.2025 08:30", <-- Час оновлення даних у локальній зоні (Europe/Kyiv)
    "today": 1765144800 <-- Unix timestamp поточного дня (Europe/Kyiv)
  },  
}