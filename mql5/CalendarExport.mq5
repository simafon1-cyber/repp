//+------------------------------------------------------------------+
//|  CalendarExport.mq5                                              |
//|  Выгрузка ВСТРОЕННОГО экономического календаря MetaTrader 5      |
//|  в файл, который читает программа на Python.                     |
//+------------------------------------------------------------------+
//
// ЗАЧЕМ ЭТО НУЖНО
// ---------------
// Календарь MetaTrader 5 полностью бесплатен: без API-ключа, без
// регистрации, без лимитов запросов. Это те же данные, что видны во
// вкладке "Календарь" в самом терминале.
//
// Но python-пакет MetaTrader5 календарь НЕ отдаёт: функции CalendarValue*
// существуют только в MQL5. Поэтому программа на Python вынуждена была
// брать новости у стороннего API (Finnhub). Этот сервис закрывает разрыв:
// он читает календарь средствами MQL5 и раз в несколько минут кладёт его
// в обычный JSON-файл, а программа этот файл читает.
//
// ЭТО СЕРВИС, А НЕ СОВЕТНИК
// -------------------------
// Сервис работает в фоне и НЕ занимает график. Ему не нужен ни символ, ни
// разрешение на автоторговлю — он ничего не торгует, только читает
// календарь и пишет файл.
//
// КАК УСТАНОВИТЬ (подробно — в install/Install-CalendarExport.ps1):
//   1. Скопировать этот файл в  <папка данных>/MQL5/Services/
//   2. В MetaEditor нажать F7 (компиляция)
//   3. В терминале: Навигатор -> Сервисы -> CalendarExport -> Добавить сервис
//   4. Правой кнопкой по нему -> Запустить
//
// КУДА ПИШЕТ
// ----------
// <папка данных>/MQL5/Files/calendar_export.json
// Программа находит этот путь сама через terminal_info().data_path —
// прописывать его руками не нужно.
//
// ЧЕГО ЭТОТ СЕРВИС НЕ ДЕЛАЕТ
// --------------------------
// Не торгует, не меняет настройки, не открывает сеть наружу. Только чтение
// календаря и запись одного файла.
//
//+------------------------------------------------------------------+
#property service
#property copyright "AI Scalper project"
#property version   "1.00"
#property description "Выгружает встроенный календарь MT5 в JSON для программы на Python"

input int    DaysBack        = 2;                       // Сколько дней назад выгружать
input int    DaysAhead       = 7;                       // Сколько дней вперёд выгружать
input int    RefreshMinutes  = 5;                       // Как часто обновлять файл, минут
input string OutputFileName  = "calendar_export.json";  // Имя файла в MQL5/Files
input bool   VerboseLog      = true;                    // Писать подробности в журнал

//+------------------------------------------------------------------+
//| Экранирование строки для JSON                                    |
//| Названия событий приходят из календаря как есть и могут содержать|
//| кавычки и обратные слэши — без экранирования файл станет битым.  |
//+------------------------------------------------------------------+
string JsonEscape(const string text)
  {
   string out = "";
   int len = StringLen(text);
   for(int i = 0; i < len; i++)
     {
      ushort ch = StringGetCharacter(text, i);
      if(ch == '"')            out += "\\\"";
      else if(ch == '\\')      out += "\\\\";
      else if(ch == '\n')      out += "\\n";
      else if(ch == '\r')      out += "\\r";
      else if(ch == '\t')      out += "\\t";
      else if(ch < 32)         out += " ";   // прочие управляющие символы
      else                     out += ShortToString(ch);
     }
   return out;
  }

//+------------------------------------------------------------------+
//| Важность события в понятную программе строку                     |
//+------------------------------------------------------------------+
string ImportanceText(const ENUM_CALENDAR_EVENT_IMPORTANCE importance)
  {
   if(importance == CALENDAR_IMPORTANCE_HIGH)     return "high";
   if(importance == CALENDAR_IMPORTANCE_MODERATE) return "medium";
   return "low";
  }

//+------------------------------------------------------------------+
//| Числовое значение показателя в строку                            |
//|                                                                  |
//| В календаре MT5 значения хранятся целым числом, умноженным на    |
//| 1 000 000, а LONG_MIN означает "значения нет" (например, факт    |
//| ещё не вышел). Пустая строка на выходе = данных нет; выдумывать  |
//| ноль вместо отсутствующего значения нельзя — программа приняла   |
//| бы его за настоящий ноль.                                        |
//+------------------------------------------------------------------+
string ValueText(const long raw, const int digits)
  {
   if(raw == LONG_MIN)
      return "";
   double v = (double)raw / 1000000.0;
   int d = (digits >= 0 && digits <= 8) ? digits : 2;
   return DoubleToString(v, d);
  }

//+------------------------------------------------------------------+
//| Время в формате, который однозначно читается на стороне Python   |
//|                                                                  |
//| ВАЖНО: календарь отдаёт время в часовом поясе ТОРГОВОГО СЕРВЕРА. |
//| Мы пишем и его, и смещение сервера относительно UTC, чтобы       |
//| программа могла привести событие к своему местному времени и не  |
//| промахнуться на несколько часов.                                 |
//+------------------------------------------------------------------+
string TimeText(const datetime t)
  {
   return TimeToString(t, TIME_DATE | TIME_MINUTES | TIME_SECONDS);
  }

//+------------------------------------------------------------------+
//| Собрать календарь и записать файл. Возвращает число событий или  |
//| -1 при ошибке записи.                                            |
//+------------------------------------------------------------------+
int ExportCalendar()
  {
   datetime now  = TimeCurrent();
   datetime from = now - (datetime)DaysBack  * 24 * 60 * 60;
   datetime to   = now + (datetime)DaysAhead * 24 * 60 * 60;

   MqlCalendarValue values[];
   // country_code=NULL и currency=NULL — берём ВСЕ страны и валюты; отбор
   // по нужной паре делает уже программа, ей виднее свои символы.
   int total = CalendarValueHistory(values, from, to, NULL, NULL);
   if(total <= 0)
     {
      int err = GetLastError();
      // 0 событий — законный результат (выходные), это не ошибка.
      if(err != 0 && err != ERR_SUCCESS)
        {
         PrintFormat("CalendarExport: календарь недоступен, ошибка %d. "
                     "Проверь, что терминал подключён к серверу и календарь "
                     "включён в настройках.", err);
         return -1;
        }
      total = 0;
     }

   // Смещение времени сервера относительно UTC — в секундах.
   // TimeGMTOffset() даёт смещение МЕСТНОГО времени, поэтому считаем разницу
   // сервер-UTC напрямую: так программа не зависит от часов компьютера.
   long server_utc_offset = (long)(TimeCurrent() - TimeGMT());

   string json = "{\n";
   json += "  \"source\": \"MetaTrader 5 built-in calendar\",\n";
   json += "  \"generated\": \"" + TimeText(TimeGMT()) + "\",\n";
   json += "  \"server_utc_offset_seconds\": " + IntegerToString(server_utc_offset) + ",\n";
   json += "  \"events\": [\n";

   int written = 0;
   for(int i = 0; i < ArraySize(values); i++)
     {
      MqlCalendarEvent event;
      if(!CalendarEventById(values[i].event_id, event))
         continue;

      MqlCalendarCountry country;
      string currency = "";
      if(CalendarCountryById(event.country_id, country))
         currency = country.currency;
      if(currency == "")
         continue;   // без валюты событие бесполезно — не с чем сопоставлять символ

      if(written > 0)
         json += ",\n";

      json += "    {";
      json += "\"time\": \""     + TimeText(values[i].time) + "\", ";
      json += "\"currency\": \"" + JsonEscape(currency) + "\", ";
      json += "\"event\": \""    + JsonEscape(event.name) + "\", ";
      json += "\"impact\": \""   + ImportanceText(event.importance) + "\", ";
      json += "\"actual\": \""   + ValueText(values[i].actual_value,   event.digits) + "\", ";
      json += "\"estimate\": \"" + ValueText(values[i].forecast_value, event.digits) + "\", ";
      json += "\"prev\": \""     + ValueText(values[i].prev_value,     event.digits) + "\"";
      json += "}";
      written++;
     }

   json += "\n  ]\n}\n";

   // Пишем во временный файл и переименовываем: программа не должна прочитать
   // файл в момент, когда он записан наполовину.
   string tmp_name = OutputFileName + ".tmp";
   int handle = FileOpen(tmp_name, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
     {
      PrintFormat("CalendarExport: не могу создать файл %s, ошибка %d",
                  tmp_name, GetLastError());
      return -1;
     }
   FileWriteString(handle, json);
   FileClose(handle);

   if(FileIsExist(OutputFileName))
      FileDelete(OutputFileName);
   if(!FileMove(tmp_name, 0, OutputFileName, FILE_REWRITE))
     {
      PrintFormat("CalendarExport: не могу переименовать %s в %s, ошибка %d",
                  tmp_name, OutputFileName, GetLastError());
      return -1;
     }

   return written;
  }

//+------------------------------------------------------------------+
//| Точка входа сервиса                                              |
//+------------------------------------------------------------------+
void OnStart()
  {
   PrintFormat("CalendarExport запущен: %s, обновление раз в %d мин, "
               "окно -%d/+%d дней",
               OutputFileName, RefreshMinutes, DaysBack, DaysAhead);

   int pause_ms = RefreshMinutes * 60 * 1000;
   if(pause_ms < 60000)
      pause_ms = 60000;   // чаще раза в минуту смысла нет: календарь так часто не меняется

   while(!IsStopped())
     {
      int count = ExportCalendar();
      if(VerboseLog)
        {
         if(count >= 0)
            PrintFormat("CalendarExport: записано событий: %d", count);
        }

      // Спим короткими шагами, чтобы остановка сервиса срабатывала быстро,
      // а не ждала полного интервала обновления.
      int slept = 0;
      while(slept < pause_ms && !IsStopped())
        {
         Sleep(1000);
         slept += 1000;
        }
     }

   Print("CalendarExport остановлен.");
  }
//+------------------------------------------------------------------+
