//+------------------------------------------------------------------+
//|                                                  DualGuardEA.mq5 |
//|  Советник для двух графиков M5: EURUSD и XAUUSD.                 |
//|  Приоритет — ограничение риска, прозрачность, работа на демо.    |
//|                                                                  |
//|  Основан на изучении открытого проекта Nyao Scalper v43.0        |
//|  (c) 2026 Elriz Wiraswara, BSD 3-Clause,                         |
//|  https://github.com/elrizwiraswara/nyao_scalper_mt5              |
//|  Механизмы martingale / hedge chain / recovery sizing            |
//|  полностью удалены (см. NOTICE.md).                              |
//+------------------------------------------------------------------+
#property copyright "DualGuard EA. Portions (c) 2026 Elriz Wiraswara (Nyao Scalper, BSD-3-Clause)"
#property link      "https://github.com/simafon1-cyber/repp"
#property version   "1.00"

#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| ПЕРЕЧИСЛЕНИЯ                                                     |
//+------------------------------------------------------------------+
enum ENUM_PROFILE_MODE
  {
   PROFILE_AUTO   = 0,  // auto — определить по символу
   PROFILE_EURUSD = 1,  // eurusd
   PROFILE_GOLD   = 2   // gold (XAUUSD)
  };

enum ENUM_BRIDGE_FAILURE
  {
   BRIDGE_PAUSE    = 0, // pause — блокировать новые входы
   BRIDGE_BASELINE = 1  // baseline — работать по локальной логике
  };

enum ENUM_AI_REGIME
  {
   REGIME_NONE = 0,     // нет данных
   REGIME_TREND_UP,     // trend_up
   REGIME_TREND_DOWN,   // trend_down
   REGIME_RANGE,        // range
   REGIME_CHAOS         // chaos
  };

enum ENUM_ENTRY_MODE
  {
   ENTRY_TREND     = 0, // тренд: EMA + RSI (как Nyao Scalper)
   ENTRY_BOLLINGER = 1, // отбой от полос Боллинджера (как Dark Venus, без мартингейла)
   ENTRY_BOTH      = 2  // оба режима
  };

//+------------------------------------------------------------------+
//| ВХОДНЫЕ ПАРАМЕТРЫ                                                |
//+------------------------------------------------------------------+
input group "=== Профиль инструмента ==="
input ENUM_PROFILE_MODE InpProfile          = PROFILE_AUTO; // Профиль инструмента
input long   InpMagicNumber                 = 0;        // MagicNumber (0 = авто: EURUSD 510001, GOLD 510002)
input long   InpPartnerMagic                = 0;        // MagicNumber второго графика (0 = авто)
input bool   InpVerboseLog                  = true;     // Подробный журнал причин решений

input group "=== Риск на сделку и лимиты ==="
input double InpRiskPercentEURUSD           = 0.5;      // EURUSD: риск на сделку, % equity
input double InpRiskPercentGold             = 0.3;      // XAUUSD: риск на сделку, % equity
input double InpMaxLot                      = 1.0;      // Максимальный лот одной сделки
input int    InpMaxPositionsPerSymbol       = 3;        // Макс. открытых позиций на инструмент
input double InpMaxPortfolioRiskPercent     = 2.0;      // Макс. суммарный открытый риск портфеля, % equity
input double InpMinAddDistanceATR           = 0.5;      // Мин. расстояние между входами, x ATR

input group "=== Общий риск двух графиков (Global Variables) ==="
input double InpDailyLossLimitPercent       = 3.0;      // Дневной лимит убытка, % от баланса начала дня
input double InpSharedBasketStopPercent     = 5.0;      // Общий корзинный стоп: плавающий убыток, % equity
input double InpBasketTakeProfitPercent     = 0.5;      // Корзинная фиксация прибыли, % equity (0 = выкл)
input int    InpBasketPauseMinutes          = 60;       // Пауза после корзинного стопа, минут
input bool   InpIncludeManualPositions      = false;    // Включать ручные сделки в общий контроль (не рекомендуется)
input bool   InpFastBasketClose             = true;     // Быстрое закрытие корзины (асинхронные запросы)

input group "=== Сигнал входа (M5, только закрытая свеча) ==="
input ENUM_ENTRY_MODE InpEntryMode          = ENTRY_TREND; // Режим входа
input int    InpBBPeriod                    = 20;       // Период полос Боллинджера (M5)
input double InpBBDeviation                 = 2.0;      // Отклонение полос Боллинджера
input double InpBBRSIBuyBelow               = 40.0;     // Боллинджер: покупка при RSI ниже
input double InpBBRSISellAbove              = 60.0;     // Боллинджер: продажа при RSI выше
input int    InpEMAFastPeriod               = 20;       // Период быстрой EMA (M5)
input int    InpEMASlowPeriod               = 50;       // Период медленной EMA (M5)
input int    InpRSIPeriod                   = 14;       // Период RSI (M5)
input double InpRSIBuyMin                   = 50.0;     // RSI: минимум для покупки
input double InpRSIBuyMax                   = 70.0;     // RSI: максимум для покупки (не покупать перекупленность)
input double InpRSISellMin                  = 30.0;     // RSI: минимум для продажи (не продавать перепроданность)
input double InpRSISellMax                  = 50.0;     // RSI: максимум для продажи
input int    InpATRPeriod                   = 14;       // Период ATR (M5)
input int    InpATRAvgLookback              = 20;       // Свечей для средней ATR (фильтр мёртвого рынка)
input double InpMinVolRatio                 = 0.6;      // Мин. ATR/среднийATR для торговли (0 = выкл)

input group "=== Фильтр направления H1 ==="
input bool   InpUseH1TrendFilter            = true;     // Включить фильтр тренда H1 (EMA 50/200)
input int    InpH1EMAFast                   = 50;       // H1: период быстрой EMA
input int    InpH1EMASlow                   = 200;      // H1: период медленной EMA

input group "=== Профиль EURUSD: SL/TP и фильтры ==="
input double InpEurSLatr                    = 1.2;      // EURUSD: Stop Loss, x ATR
input double InpEurTPatr                    = 1.8;      // EURUSD: Take Profit, x ATR
input double InpEurTrailATR                 = 1.0;      // EURUSD: трейлинг, x ATR
input double InpEurBEatr                    = 0.8;      // EURUSD: безубыток после прибыли, x ATR
input double InpEurMaxSpreadPoints          = 15;       // EURUSD: макс. спред, пункты (0 = авто от ATR)
input double InpEurMaxSpreadATRRatio        = 0.25;     // EURUSD: авто-порог спреда как доля ATR
input bool   InpEurUseSessions              = false;    // EURUSD: ограничить торговые сессии
input int    InpEurNewsMinutes              = 30;       // EURUSD: блок новостей (USD и EUR high), +/- минут

input group "=== Профиль XAUUSD (золото): SL/TP и фильтры ==="
input double InpGoldSLatr                   = 1.5;      // GOLD: Stop Loss, x ATR
input double InpGoldTPatr                   = 2.2;      // GOLD: Take Profit, x ATR
input double InpGoldTrailATR                = 1.0;      // GOLD: трейлинг, x ATR
input double InpGoldBEatr                   = 0.8;      // GOLD: безубыток после прибыли, x ATR
input double InpGoldMaxSpreadATRRatio       = 0.20;     // GOLD: макс. спред как доля ATR(14) M5
input int    InpGoldNewsMinutes             = 45;       // GOLD: блок новостей США high, +/- минут (мин. 45)
input bool   InpGoldUseSessions             = true;     // GOLD: торговать только Лондон + Нью-Йорк
input string InpGoldSessionStartLondon      = "08:00";  // GOLD: начало (лондонское время)
input string InpGoldSessionEndNewYork       = "17:00";  // GOLD: конец (нью-йоркское время)

input group "=== Время и закрытие недели ==="
input int    InpTesterUTCOffsetHours        = 2;        // Смещение сервера от UTC для ТЕСТЕРА, часов
input int    InpMinutesBeforeWeekClose      = 40;       // Не открывать за N минут до закрытия недели
input bool   InpNewsFailOpen                = false;    // Торговать золото, если календарь MT5 недоступен (не рекомендуется)

input group "=== ИИ-мост (Python + Claude) ==="
input bool   InpEnableAI                    = true;     // Включить ИИ-ограничитель (мост)
input string InpBridgeURL                   = "http://127.0.0.1:8080"; // Адрес моста
input int    InpBridgePollMinutes           = 30;       // Опрос моста, минут
input int    InpAICacheTTLMinutes           = 45;       // Срок жизни последнего ответа, минут
input ENUM_BRIDGE_FAILURE InpBridgeFailureMode = BRIDGE_PAUSE; // Если мост НИКОГДА не отвечал

//+------------------------------------------------------------------+
//| ГЛОБАЛЬНОЕ СОСТОЯНИЕ                                             |
//+------------------------------------------------------------------+
CTrade  g_trade;

// Профиль
ENUM_PROFILE_MODE g_profile      = PROFILE_AUTO; // фактический профиль
bool     g_profileValid          = false;        // разрешена ли торговля профилем
string   g_profileReason         = "";           // причина запрета
string   g_canonSymbol           = "";           // "EURUSD" или "XAUUSD" для моста
long     g_magic                 = 0;            // мой MagicNumber
long     g_partnerMagic          = 0;            // MagicNumber второго графика

// Хэндлы индикаторов (создаются один раз в OnInit)
int      g_hEmaFast = INVALID_HANDLE;
int      g_hEmaSlow = INVALID_HANDLE;
int      g_hRsi     = INVALID_HANDLE;
int      g_hAtr     = INVALID_HANDLE;
int      g_hH1Fast  = INVALID_HANDLE;
int      g_hH1Slow  = INVALID_HANDLE;
int      g_hBB      = INVALID_HANDLE;

// Кэш значений индикаторов по закрытой свече (обновляется раз в свечу)
double   g_emaFast=0, g_emaSlow=0, g_rsi=0, g_atr=0, g_atrAvg=0;
double   g_h1Fast=0, g_h1Slow=0;
double   g_bbUpper=0, g_bbLower=0;
double   g_closedOpen=0, g_closedClose=0, g_closedHigh=0, g_closedLow=0;
bool     g_indReady = false;
datetime g_lastBarTime = 0;

// Состояние ИИ-моста
ENUM_AI_REGIME g_aiRegime        = REGIME_NONE;
bool     g_aiTradeAllowed        = true;
double   g_aiRiskMultiplier      = 1.0;
string   g_aiReason              = "";
datetime g_aiLastOkTime          = 0;   // время последнего КОРРЕКТНОГО ответа
datetime g_aiNextPollTime        = 0;
bool     g_aiEnabled             = false; // фактическое состояние (в тестере выключается)
bool     g_webRequestErrorShown  = false;

// Новости (кэш, обновляется по таймеру)
datetime g_newsTimes[];
int      g_newsCount             = 0;
bool     g_calendarAvailable     = true;
datetime g_newsNextRefresh       = 0;

// Общий риск: имена глобальных переменных терминала
string   g_gvPrefix              = "";
string   g_gvLock                = "";
string   g_gvDaySerial           = "";
string   g_gvDayBalance          = "";
string   g_gvBlockedDay          = "";
string   g_gvPauseUntil          = "";

// Прочее
string   g_lastBlockReason       = "";
datetime g_lastEntryBarTime      = 0;

//+------------------------------------------------------------------+
//| ЖУРНАЛ                                                           |
//+------------------------------------------------------------------+
void Log(const string msg)
  {
   Print("[DualGuard ", g_canonSymbol, "] ", msg);
  }

// Причины блокировки входа печатаем только при изменении, чтобы не спамить
void LogBlock(const string reason)
  {
   if(reason != g_lastBlockReason)
     {
      g_lastBlockReason = reason;
      if(InpVerboseLog)
         Log("Вход запрещён: " + reason);
     }
  }

//+------------------------------------------------------------------+
//| ПРОФИЛЬ ИНСТРУМЕНТА                                              |
//+------------------------------------------------------------------+
// Определяем профиль по символу с учётом суффиксов брокера
// (EURUSD.a, XAUUSDm, GOLD.raw и т.п.)
ENUM_PROFILE_MODE DetectProfileBySymbol(const string symbol)
  {
   string base   = SymbolInfoString(symbol, SYMBOL_CURRENCY_BASE);
   string profit = SymbolInfoString(symbol, SYMBOL_CURRENCY_PROFIT);
   StringToUpper(base);
   StringToUpper(profit);

   // Самый надёжный признак — валюты символа
   if(base == "XAU" && profit == "USD")
      return PROFILE_GOLD;
   if(base == "EUR" && profit == "USD")
      return PROFILE_EURUSD;

   // Запасной вариант — имя символа
   string name = symbol;
   StringToUpper(name);
   if(StringFind(name, "XAUUSD") >= 0 || StringFind(name, "GOLD") >= 0)
      return PROFILE_GOLD;
   if(StringFind(name, "EURUSD") >= 0)
      return PROFILE_EURUSD;

   return PROFILE_AUTO; // не распознан
  }

void InitProfile()
  {
   ENUM_PROFILE_MODE detected = DetectProfileBySymbol(_Symbol);

   if(InpProfile == PROFILE_AUTO)
     {
      if(detected == PROFILE_AUTO)
        {
         g_profileValid  = false;
         g_profileReason = "Символ '" + _Symbol + "' не распознан как EURUSD или XAUUSD. Новые сделки запрещены.";
         return;
        }
      g_profile = detected;
     }
   else
     {
      // Профиль задан вручную — он обязан соответствовать символу графика
      if(detected != InpProfile)
        {
         g_profileValid  = false;
         g_profileReason = "Профиль '" + EnumToString(InpProfile) +
                           "' не соответствует символу графика '" + _Symbol +
                           "'. Новые сделки запрещены.";
         return;
        }
      g_profile = InpProfile;
     }

   g_canonSymbol  = (g_profile == PROFILE_GOLD ? "XAUUSD" : "EURUSD");
   g_profileValid = true;

   // MagicNumber: 0 = авто по профилю
   g_magic        = (InpMagicNumber  != 0) ? InpMagicNumber
                    : (g_profile == PROFILE_GOLD ? 510002 : 510001);
   g_partnerMagic = (InpPartnerMagic != 0) ? InpPartnerMagic
                    : (g_profile == PROFILE_GOLD ? 510001 : 510002);
  }

// Параметры активного профиля
double ProfileRiskPercent()   { return (g_profile==PROFILE_GOLD ? InpRiskPercentGold : InpRiskPercentEURUSD); }
double ProfileSLatr()         { return (g_profile==PROFILE_GOLD ? InpGoldSLatr       : InpEurSLatr); }
double ProfileTPatr()         { return (g_profile==PROFILE_GOLD ? InpGoldTPatr       : InpEurTPatr); }
double ProfileTrailATR()      { return (g_profile==PROFILE_GOLD ? InpGoldTrailATR    : InpEurTrailATR); }
double ProfileBEatr()         { return (g_profile==PROFILE_GOLD ? InpGoldBEatr       : InpEurBEatr); }
bool   ProfileUseSessions()   { return (g_profile==PROFILE_GOLD ? InpGoldUseSessions : InpEurUseSessions); }
int    ProfileNewsMinutes()
  {
   if(g_profile==PROFILE_GOLD)
      return MathMax(InpGoldNewsMinutes, 45); // для золота минимум 45 минут — жёсткое правило
   return InpEurNewsMinutes;
  }

//+------------------------------------------------------------------+
//| ВРЕМЯ: GMT, летнее время, сессии                                 |
//+------------------------------------------------------------------+
datetime GetGMT()
  {
   // На живом графике TimeGMT() даёт точное всемирное время.
   // В тестере стратегий TimeGMT() ненадёжно — используем ручное смещение.
   if((bool)MQLInfoInteger(MQL_TESTER))
      return TimeCurrent() - InpTesterUTCOffsetHours * 3600;
   return TimeGMT();
  }

int DayOfWeekOf(datetime t)
  {
   MqlDateTime st;
   TimeToStruct(t, st);
   return st.day_of_week;
  }

// Дата (полночь UTC) для года-месяца-дня
datetime MakeDate(int year, int month, int day)
  {
   MqlDateTime st;
   st.year=year; st.mon=month; st.day=day;
   st.hour=0; st.min=0; st.sec=0;
   return StructToTime(st);
  }

datetime LastSundayOfMonth(int year, int month)
  {
   int lastDay = 31;
   if(month==4 || month==6 || month==9 || month==11) lastDay=30;
   if(month==2) lastDay = ((year%4==0 && year%100!=0) || year%400==0) ? 29 : 28;
   datetime d = MakeDate(year, month, lastDay);
   while(DayOfWeekOf(d) != 0)
      d -= 86400;
   return d;
  }

datetime NthSundayOfMonth(int year, int month, int n)
  {
   datetime d = MakeDate(year, month, 1);
   while(DayOfWeekOf(d) != 0)
      d += 86400;
   return d + (n-1) * 7 * 86400;
  }

// Летнее время Европы: с последнего воскресенья марта по последнее воскресенье октября
bool IsEUDst(datetime gmt)
  {
   MqlDateTime st;
   TimeToStruct(gmt, st);
   datetime start = LastSundayOfMonth(st.year, 3)  + 1*3600; // 01:00 UTC
   datetime end   = LastSundayOfMonth(st.year, 10) + 1*3600;
   return (gmt >= start && gmt < end);
  }

// Летнее время США: со 2-го воскресенья марта по 1-е воскресенье ноября
bool IsUSDst(datetime gmt)
  {
   MqlDateTime st;
   TimeToStruct(gmt, st);
   datetime start = NthSundayOfMonth(st.year, 3, 2) + 7*3600;  // ~02:00 по Нью-Йорку
   datetime end   = NthSundayOfMonth(st.year, 11, 1) + 6*3600;
   return (gmt >= start && gmt < end);
  }

// "08:30" -> минуты от полуночи; -1 при ошибке
int ParseHHMM(const string s)
  {
   string parts[];
   if(StringSplit(s, ':', parts) != 2)
      return -1;
   int h = (int)StringToInteger(parts[0]);
   int m = (int)StringToInteger(parts[1]);
   if(h<0 || h>23 || m<0 || m>59)
      return -1;
   return h*60 + m;
  }

// Торговая сессия для золота: от открытия Лондона до закрытия Нью-Йорка.
// Время задаётся в местном времени бирж, переводится в GMT с учётом
// летнего времени — это самый надёжный вариант: смещение сервера брокера
// вообще не участвует, всё считается от всемирного времени.
bool IsInGoldSession(datetime gmt, string &reason)
  {
   int startLocal = ParseHHMM(InpGoldSessionStartLondon);
   int endLocal   = ParseHHMM(InpGoldSessionEndNewYork);
   if(startLocal < 0) startLocal = 8*60;
   if(endLocal   < 0) endLocal   = 17*60;

   int londonOffset = IsEUDst(gmt) ? 60 : 0;          // Лондон = GMT или GMT+1
   int nyOffset     = (IsUSDst(gmt) ? -4 : -5) * 60;  // Нью-Йорк = GMT-5 или GMT-4

   int startGMT = startLocal - londonOffset;          // начало в минутах GMT
   int endGMT   = endLocal   - nyOffset;              // конец в минутах GMT

   MqlDateTime st;
   TimeToStruct(gmt, st);
   int nowMin = st.hour*60 + st.min;

   if(nowMin < startGMT || nowMin >= endGMT)
     {
      reason = StringFormat("вне сессии Лондон+Нью-Йорк (сейчас %02d:%02d GMT, окно %02d:%02d-%02d:%02d GMT)",
                            st.hour, st.min, startGMT/60, startGMT%60, endGMT/60, endGMT%60);
      return false;
     }
   return true;
  }

// Фильтр закрытия недели: не открываться в пятницу перед закрытием рынка
bool IsNearWeekClose(datetime gmt, string &reason)
  {
   MqlDateTime st;
   TimeToStruct(gmt, st);
   if(st.day_of_week != 5) // пятница
      return false;
   // Закрытие рынка ~17:00 Нью-Йорка = 22:00 GMT зимой, 21:00 GMT летом
   int closeMin = 22*60 - (IsUSDst(gmt) ? 60 : 0);
   int nowMin   = st.hour*60 + st.min;
   if(nowMin >= closeMin - InpMinutesBeforeWeekClose)
     {
      reason = "конец недели: до закрытия рынка меньше " + IntegerToString(InpMinutesBeforeWeekClose) + " минут";
      return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| ФИЛЬТР НОВОСТЕЙ (встроенный календарь MT5)                       |
//+------------------------------------------------------------------+
void CollectNewsForCountry(const string country)
  {
   MqlCalendarValue values[];
   datetime from = TimeTradeServer() - 12*3600;
   datetime to   = TimeTradeServer() + 24*3600;
   if(!CalendarValueHistory(values, from, to, country))
     {
      g_calendarAvailable = false;
      return;
     }
   int total = ArraySize(values);
   for(int i=0; i<total; i++)
     {
      MqlCalendarEvent ev;
      if(!CalendarEventById(values[i].event_id, ev))
         continue;
      if(ev.importance != CALENDAR_IMPORTANCE_HIGH)
         continue;
      int n = ArraySize(g_newsTimes);
      ArrayResize(g_newsTimes, n+1);
      g_newsTimes[n] = values[i].time;
     }
  }

void RefreshNewsCache()
  {
   ArrayResize(g_newsTimes, 0);
   g_calendarAvailable = true;

   if((bool)MQLInfoInteger(MQL_TESTER))
     {
      // В тестере календарь недоступен — фильтр отключается с предупреждением
      g_calendarAvailable = false;
      return;
     }

   if(g_profile == PROFILE_GOLD)
      CollectNewsForCountry("US");           // золото: только новости США
   else
     {
      CollectNewsForCountry("US");           // EURUSD: США и еврозона
      CollectNewsForCountry("EU");
     }
   g_newsCount = ArraySize(g_newsTimes);
   g_newsNextRefresh = TimeCurrent() + 15*60;
   if(InpVerboseLog)
      Log("Календарь новостей обновлён: событий high-impact в окне +/-сутки: " + IntegerToString(g_newsCount));
  }

bool IsNewsBlock(string &reason)
  {
   int minutes = ProfileNewsMinutes();

   if(!g_calendarAvailable)
     {
      if((bool)MQLInfoInteger(MQL_TESTER))
         return false; // в тестере фильтр не работает — задокументировано
      if(g_profile == PROFILE_GOLD && !InpNewsFailOpen)
        {
         reason = "календарь новостей MT5 недоступен, для золота вход запрещён (безопасный отказ)";
         return true;
        }
      return false;
     }

   datetime now = TimeTradeServer();
   int total = ArraySize(g_newsTimes);
   for(int i=0; i<total; i++)
     {
      if(now >= g_newsTimes[i] - minutes*60 && now <= g_newsTimes[i] + minutes*60)
        {
         reason = "новости high-impact в " + TimeToString(g_newsTimes[i], TIME_MINUTES) +
                  " (блок +/-" + IntegerToString(minutes) + " мин)";
         return true;
        }
     }
   return false;
  }

//+------------------------------------------------------------------+
//| ФИЛЬТР СПРЕДА                                                    |
//+------------------------------------------------------------------+
bool IsSpreadTooWide(string &reason)
  {
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double spread = ask - bid;
   if(spread <= 0)
      return false;

   if(g_profile == PROFILE_GOLD)
     {
      // Правило золота: спред не больше доли ATR(14) на M5
      if(g_atr > 0 && spread > InpGoldMaxSpreadATRRatio * g_atr)
        {
         reason = StringFormat("спред %.1f пт > %.2f x ATR (%.1f пт)",
                               spread/_Point, InpGoldMaxSpreadATRRatio, InpGoldMaxSpreadATRRatio*g_atr/_Point);
         return true;
        }
      return false;
     }

   // EURUSD: фиксированный порог в пунктах, 0 = авто от ATR
   double maxSpread = InpEurMaxSpreadPoints * _Point;
   if(InpEurMaxSpreadPoints <= 0 && g_atr > 0)
      maxSpread = InpEurMaxSpreadATRRatio * g_atr;
   if(maxSpread > 0 && spread > maxSpread)
     {
      reason = StringFormat("спред %.1f пт > порога %.1f пт", spread/_Point, maxSpread/_Point);
      return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| ИНДИКАТОРЫ: кэш по закрытой свече                                |
//+------------------------------------------------------------------+
bool UpdateIndicatorCache()
  {
   double buf[];
   ArraySetAsSeries(buf, true);

   // Значения берём по индексу 1 — последняя ЗАКРЫТАЯ свеча M5
   if(CopyBuffer(g_hEmaFast, 0, 1, 1, buf) < 1) return false;
   g_emaFast = buf[0];
   if(CopyBuffer(g_hEmaSlow, 0, 1, 1, buf) < 1) return false;
   g_emaSlow = buf[0];
   if(CopyBuffer(g_hRsi, 0, 1, 1, buf) < 1) return false;
   g_rsi = buf[0];

   double atrBuf[];
   ArraySetAsSeries(atrBuf, true);
   int need = InpATRAvgLookback + 1;
   if(CopyBuffer(g_hAtr, 0, 1, need, atrBuf) < need) return false;
   g_atr = atrBuf[0];
   double sum = 0;
   for(int i=0; i<InpATRAvgLookback; i++)
      sum += atrBuf[i];
   g_atrAvg = sum / InpATRAvgLookback;

   if(InpUseH1TrendFilter)
     {
      if(CopyBuffer(g_hH1Fast, 0, 1, 1, buf) < 1) return false;
      g_h1Fast = buf[0];
      if(CopyBuffer(g_hH1Slow, 0, 1, 1, buf) < 1) return false;
      g_h1Slow = buf[0];
     }

   if(InpEntryMode != ENTRY_TREND)
     {
      if(CopyBuffer(g_hBB, UPPER_BAND, 1, 1, buf) < 1) return false;
      g_bbUpper = buf[0];
      if(CopyBuffer(g_hBB, LOWER_BAND, 1, 1, buf) < 1) return false;
      g_bbLower = buf[0];
     }

   g_closedOpen  = iOpen(_Symbol, PERIOD_M5, 1);
   g_closedClose = iClose(_Symbol, PERIOD_M5, 1);
   g_closedHigh  = iHigh(_Symbol, PERIOD_M5, 1);
   g_closedLow   = iLow(_Symbol, PERIOD_M5, 1);

   g_indReady = (g_atr > 0);
   return g_indReady;
  }

//+------------------------------------------------------------------+
//| СИГНАЛ ВХОДА (прозрачные правила, только закрытая свеча M5)      |
//| Упрощённое ядро скоринга Nyao Scalper:                           |
//|   тренд EMA + импульс RSI + фильтр мёртвого рынка ATR            |
//+------------------------------------------------------------------+
int GetEntrySignal(string &why)
  {
   if(!g_indReady)
     {
      why = "индикаторы не готовы";
      return 0;
     }

   // Фильтр мёртвого рынка: волатильность упала — не торгуем
   if(InpMinVolRatio > 0 && g_atrAvg > 0 && g_atr / g_atrAvg < InpMinVolRatio)
     {
      why = StringFormat("мёртвый рынок: ATR/среднийATR = %.2f < %.2f", g_atr/g_atrAvg, InpMinVolRatio);
      return 0;
     }

   // --- Режим "тренд" (упрощённое ядро Nyao Scalper)
   if(InpEntryMode == ENTRY_TREND || InpEntryMode == ENTRY_BOTH)
     {
      bool bullTrend   = (g_emaFast > g_emaSlow) && (g_closedClose > g_emaFast);
      bool bearTrend   = (g_emaFast < g_emaSlow) && (g_closedClose < g_emaFast);
      bool bullCandle  = (g_closedClose > g_closedOpen);
      bool bearCandle  = (g_closedClose < g_closedOpen);
      bool rsiBuyZone  = (g_rsi >= InpRSIBuyMin  && g_rsi <= InpRSIBuyMax);
      bool rsiSellZone = (g_rsi >= InpRSISellMin && g_rsi <= InpRSISellMax);

      if(bullTrend && bullCandle && rsiBuyZone)
        {
         why = StringFormat("BUY тренд: EMA%d>EMA%d, закрытие выше EMA, RSI=%.1f, бычья свеча",
                            InpEMAFastPeriod, InpEMASlowPeriod, g_rsi);
         return 1;
        }
      if(bearTrend && bearCandle && rsiSellZone)
        {
         why = StringFormat("SELL тренд: EMA%d<EMA%d, закрытие ниже EMA, RSI=%.1f, медвежья свеча",
                            InpEMAFastPeriod, InpEMASlowPeriod, g_rsi);
         return -1;
        }
     }

   // --- Режим "отбой от полос Боллинджера" (идея Dark Venus, БЕЗ мартингейла:
   //     лот всегда считается от риска и после убытка не растёт)
   if(InpEntryMode == ENTRY_BOLLINGER || InpEntryMode == ENTRY_BOTH)
     {
      if(g_bbUpper > 0 && g_bbLower > 0)
        {
         // Покупка: свеча проколола нижнюю полосу, но ЗАКРЫЛАСЬ обратно выше неё
         if(g_closedLow <= g_bbLower && g_closedClose > g_bbLower && g_rsi < InpBBRSIBuyBelow)
           {
            why = StringFormat("BUY отбой: прокол нижней полосы BB и возврат, RSI=%.1f", g_rsi);
            return 1;
           }
         // Продажа: свеча проколола верхнюю полосу, но закрылась обратно ниже неё
         if(g_closedHigh >= g_bbUpper && g_closedClose < g_bbUpper && g_rsi > InpBBRSISellAbove)
           {
            why = StringFormat("SELL отбой: прокол верхней полосы BB и возврат, RSI=%.1f", g_rsi);
            return -1;
           }
        }
     }

   why = "нет сигнала на закрытой свече";
   return 0;
  }

// Фильтр направления H1: EMA50/200
bool H1AllowsDirection(int dir, string &reason)
  {
   if(!InpUseH1TrendFilter)
      return true;
   if(g_h1Fast <= 0 || g_h1Slow <= 0)
     {
      reason = "фильтр H1: данные не готовы";
      return false;
     }
   if(dir > 0 && g_h1Fast < g_h1Slow)
     {
      reason = StringFormat("фильтр H1: EMA%d ниже EMA%d — покупки запрещены", InpH1EMAFast, InpH1EMASlow);
      return false;
     }
   if(dir < 0 && g_h1Fast > g_h1Slow)
     {
      reason = StringFormat("фильтр H1: EMA%d выше EMA%d — продажи запрещены", InpH1EMAFast, InpH1EMASlow);
      return false;
     }
   return true;
  }

//+------------------------------------------------------------------+
//| ИИ-МОСТ: WebRequest, разбор JSON, кэш                            |
//+------------------------------------------------------------------+
// Достаёт "сырое" значение ключа из плоского JSON.
// Достаточно для строго заданного формата ответа моста.
bool JsonGetRaw(const string json, const string key, string &out)
  {
   string pattern = "\"" + key + "\"";
   int p = StringFind(json, pattern);
   if(p < 0) return false;
   p = StringFind(json, ":", p + StringLen(pattern));
   if(p < 0) return false;
   p++;
   int len = StringLen(json);
   while(p < len)
     {
      ushort c = StringGetCharacter(json, p);
      if(c != ' ' && c != '\t' && c != '\r' && c != '\n')
         break;
      p++;
     }
   if(p >= len) return false;

   if(StringGetCharacter(json, p) == '"')
     {
      int endq = StringFind(json, "\"", p+1);
      if(endq < 0) return false;
      out = StringSubstr(json, p+1, endq-p-1);
      return true;
     }
   int end = p;
   while(end < len)
     {
      ushort c = StringGetCharacter(json, end);
      if(c == ',' || c == '}' || c == ' ' || c == '\r' || c == '\n')
         break;
      end++;
     }
   out = StringSubstr(json, p, end-p);
   return true;
  }

// Строгая проверка ответа моста. Любое отклонение = ответ недействителен.
bool ParseBridgeResponse(const string json,
                         ENUM_AI_REGIME &regime, bool &tradeAllowed,
                         double &riskMult, string &reason)
  {
   string sRegime, sAllowed, sMult, sReason;
   if(!JsonGetRaw(json, "regime", sRegime))          return false;
   if(!JsonGetRaw(json, "trade_allowed", sAllowed))  return false;
   if(!JsonGetRaw(json, "risk_multiplier", sMult))   return false;
   if(!JsonGetRaw(json, "reason", sReason))          sReason = "";

   if(sRegime == "trend_up")        regime = REGIME_TREND_UP;
   else if(sRegime == "trend_down") regime = REGIME_TREND_DOWN;
   else if(sRegime == "range")      regime = REGIME_RANGE;
   else if(sRegime == "chaos")      regime = REGIME_CHAOS;
   else return false;

   if(sAllowed == "true")       tradeAllowed = true;
   else if(sAllowed == "false") tradeAllowed = false;
   else return false;

   riskMult = StringToDouble(sMult);
   if(riskMult < 0.0 || riskMult > 1.0)
      return false;

   reason = sReason;
   return true;
  }

void PollBridge()
  {
   if(!g_aiEnabled)
      return;

   string url = InpBridgeURL + "/regime?symbol=" + g_canonSymbol;
   char data[], result[];
   string resultHeaders;
   ResetLastError();
   int status = WebRequest("GET", url, "", 3000, data, result, resultHeaders);

   if(status == -1)
     {
      int err = GetLastError();
      if(err == 4014 && !g_webRequestErrorShown)
        {
         g_webRequestErrorShown = true;
         Log("ОШИБКА: WebRequest запрещён. Добавьте '" + InpBridgeURL +
             "' в Сервис -> Настройки -> Советники -> Разрешить WebRequest.");
        }
      else if(InpVerboseLog)
         Log("Мост недоступен (ошибка " + IntegerToString(err) + "). Возраст кэша: " +
             IntegerToString(g_aiLastOkTime>0 ? (int)((TimeCurrent()-g_aiLastOkTime)/60) : -1) + " мин");
      return;
     }

   string body = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   if(status != 200)
     {
      Log("Мост ответил кодом " + IntegerToString(status) + ": " + StringSubstr(body, 0, 200));
      return;
     }

   ENUM_AI_REGIME regime;
   bool allowed;
   double mult;
   string reason;
   if(!ParseBridgeResponse(body, regime, allowed, mult, reason))
     {
      Log("Ответ моста НЕДЕЙСТВИТЕЛЕН (неверный JSON/поля/диапазон): " + StringSubstr(body, 0, 200));
      return;
     }

   g_aiRegime         = regime;
   g_aiTradeAllowed   = allowed;
   g_aiRiskMultiplier = mult;
   g_aiReason         = reason;
   g_aiLastOkTime     = TimeCurrent();
   Log(StringFormat("Ответ ИИ: режим=%s, торговля=%s, множитель=%.2f, причина: %s",
                    EnumToString(regime), (allowed?"да":"нет"), mult, reason));
  }

// Итоговое решение ИИ-ограничителя с учётом возраста кэша.
// ИИ может только ЗАПРЕЩАТЬ и УМЕНЬШАТЬ — никогда не расширяет права.
void GetAIGate(bool &allowNew, bool &allowBuy, bool &allowSell, double &mult, string &why)
  {
   allowNew = true;
   allowBuy = true;
   allowSell = true;
   mult = 1.0;
   why = "ИИ выключен, локальная логика";

   if(!g_aiEnabled)
      return;

   if(g_aiLastOkTime == 0)
     {
      // Мост не отвечал никогда — применяем BridgeFailureMode
      if(InpBridgeFailureMode == BRIDGE_PAUSE)
        {
         allowNew = false;
         why = "мост ни разу не ответил, режим pause — входы запрещены";
        }
      else
         why = "мост ни разу не ответил, режим baseline — локальная логика";
      return;
     }

   int ageMin = (int)((TimeCurrent() - g_aiLastOkTime) / 60);
   if(ageMin >= InpAICacheTTLMinutes)
     {
      allowNew = false;
      why = StringFormat("кэш ИИ устарел (%d мин >= %d) — считаем режим chaos, входы запрещены",
                         ageMin, InpAICacheTTLMinutes);
      return;
     }

   mult = MathMin(1.0, MathMax(0.0, g_aiRiskMultiplier));

   if(!g_aiTradeAllowed || g_aiRegime == REGIME_CHAOS)
     {
      allowNew = false;
      why = "ИИ: " + (g_aiRegime==REGIME_CHAOS ? "режим chaos" : "trade_allowed=false") +
            " (" + g_aiReason + ")";
      return;
     }
   if(g_aiRegime == REGIME_TREND_UP)
     {
      allowSell = false;
      why = "ИИ: trend_up — продажи запрещены (" + g_aiReason + ")";
     }
   else if(g_aiRegime == REGIME_TREND_DOWN)
     {
      allowBuy = false;
      why = "ИИ: trend_down — покупки запрещены (" + g_aiReason + ")";
     }
   else
      why = StringFormat("ИИ: range, множитель риска %.2f (%s)", mult, g_aiReason);
  }

//+------------------------------------------------------------------+
//| ОБЩИЙ РИСК: Global Variables терминала + защита от гонок         |
//+------------------------------------------------------------------+
void InitSharedRiskNames()
  {
   // Имя включает номер счёта и сервер, чтобы состояния разных счетов не пересекались.
   long login = AccountInfoInteger(ACCOUNT_LOGIN);
   string server = AccountInfoString(ACCOUNT_SERVER);
   // Короткая контрольная сумма имени сервера (имя GV ограничено по длине)
   int hash = 0;
   for(int i=0; i<StringLen(server); i++)
      hash = (hash*31 + StringGetCharacter(server, i)) & 0x7FFFFFFF;

   g_gvPrefix     = "DG." + IntegerToString(login) + "." + IntegerToString(hash) + ".";
   g_gvLock       = g_gvPrefix + "lock";
   g_gvDaySerial  = g_gvPrefix + "day";
   g_gvDayBalance = g_gvPrefix + "daybal";
   g_gvBlockedDay = g_gvPrefix + "blocked";
   g_gvPauseUntil = g_gvPrefix + "pause";
  }

// Атомарная блокировка через GlobalVariableSetOnCondition (0 -> 1).
// Защищает от одновременной записи с двух графиков.
bool AcquireLock(int timeoutMs = 2000)
  {
   if(!GlobalVariableCheck(g_gvLock))
      GlobalVariableSet(g_gvLock, 0.0);

   uint start = GetTickCount();
   while((int)(GetTickCount() - start) < timeoutMs)
     {
      if(GlobalVariableSetOnCondition(g_gvLock, 1.0, 0.0))
         return true;
      // Защита от зависшей блокировки: если lock=1 дольше 30 секунд — сбросить
      if(GlobalVariableCheck(g_gvLock) &&
         GlobalVariableGet(g_gvLock) > 0.5 &&
         TimeCurrent() - GlobalVariableTime(g_gvLock) > 30)
         GlobalVariableSet(g_gvLock, 0.0);
      Sleep(20);
     }
   return false;
  }

void ReleaseLock()
  {
   GlobalVariableSet(g_gvLock, 0.0);
  }

long TodaySerial()
  {
   return (long)(TimeTradeServer() / 86400);
  }

double GVGet(const string name, double def = 0.0)
  {
   if(!GlobalVariableCheck(name))
      return def;
   return GlobalVariableGet(name);
  }

// Позиция принадлежит роботу (мой magic или magic второго графика)?
bool IsRobotPosition(long magic)
  {
   if(magic == g_magic || magic == g_partnerMagic)
      return true;
   if(InpIncludeManualPositions && magic == 0)
      return true;
   return false;
  }

// Совокупный плавающий результат всех позиций робота (оба символа)
double RobotFloatingPL()
  {
   double total = 0;
   int n = PositionsTotal();
   for(int i=0; i<n; i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsRobotPosition((long)PositionGetInteger(POSITION_MAGIC)))
         continue;
      total += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
     }
   return total;
  }

// Быстрое закрытие всей корзины робота.
// В асинхронном режиме все запросы уходят на сервер сразу — позиции
// закрываются практически одновременно (рекомендация форума MQL5).
void CloseAllRobotPositions(const string reason)
  {
   Log("ЗАКРЫТИЕ ВСЕЙ КОРЗИНЫ: " + reason);
   if(InpFastBasketClose)
      g_trade.SetAsyncMode(true);

   for(int i=PositionsTotal()-1; i>=0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsRobotPosition((long)PositionGetInteger(POSITION_MAGIC)))
         continue;
      if(!g_trade.PositionClose(ticket))
         Log("Не удалось закрыть позицию #" + IntegerToString((long)ticket) +
             ": " + IntegerToString((int)g_trade.ResultRetcode()));
     }

   if(InpFastBasketClose)
      g_trade.SetAsyncMode(false);
  }

// Повторная попытка закрытия остатков (асинхронные запросы могли не пройти)
void RetryCloseIfBlocked()
  {
   long today = TodaySerial();
   bool blocked = ((long)GVGet(g_gvBlockedDay, -1) == today);
   bool paused  = (TimeCurrent() < (datetime)(long)GVGet(g_gvPauseUntil, 0));
   if(!blocked && !paused)
      return;
   for(int i=PositionsTotal()-1; i>=0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsRobotPosition((long)PositionGetInteger(POSITION_MAGIC)))
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue; // каждый экземпляр дозакрывает свой символ
      g_trade.PositionClose(ticket);
     }
  }

// Главная проверка общего риска. Вызывается на каждом тике (дёшево).
void CheckSharedRisk()
  {
   long today = TodaySerial();

   // 1. Начало нового торгового дня: первый заметивший сохраняет баланс дня
   if((long)GVGet(g_gvDaySerial, -1) != today)
     {
      if(AcquireLock())
        {
         if((long)GVGet(g_gvDaySerial, -1) != today) // повторная проверка под блокировкой
           {
            GlobalVariableSet(g_gvDaySerial, (double)today);
            GlobalVariableSet(g_gvDayBalance, AccountInfoDouble(ACCOUNT_BALANCE));
            Log("Новый торговый день. Баланс начала дня: " +
                DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2));
           }
         ReleaseLock();
        }
     }

   double dayBalance = GVGet(g_gvDayBalance, 0);
   double equity     = AccountInfoDouble(ACCOUNT_EQUITY);

   // 2. Дневной лимит убытка (с учётом плавающего убытка через equity)
   bool blockedToday = ((long)GVGet(g_gvBlockedDay, -1) == today);
   if(!blockedToday && dayBalance > 0 && InpDailyLossLimitPercent > 0)
     {
      double lossPct = (dayBalance - equity) / dayBalance * 100.0;
      if(lossPct >= InpDailyLossLimitPercent)
        {
         if(AcquireLock())
           {
            if((long)GVGet(g_gvBlockedDay, -1) != today)
              {
               GlobalVariableSet(g_gvBlockedDay, (double)today);
               Log(StringFormat("СРАБОТАЛ ДНЕВНОЙ ЛИМИТ: потеря %.2f%% от баланса дня %.2f. Блокировка до следующего дня.",
                                lossPct, dayBalance));
              }
            ReleaseLock();
           }
         CloseAllRobotPositions("дневной лимит убытка " + DoubleToString(InpDailyLossLimitPercent,1) + "%");
        }
     }

   // 3. Общий корзинный стоп по плавающему убытку обоих инструментов
   double floating = RobotFloatingPL();
   if(InpSharedBasketStopPercent > 0 && floating < 0 &&
      -floating >= equity * InpSharedBasketStopPercent / 100.0)
     {
      datetime pauseUntil = (datetime)(long)GVGet(g_gvPauseUntil, 0);
      if(TimeCurrent() >= pauseUntil)
        {
         if(AcquireLock())
           {
            GlobalVariableSet(g_gvPauseUntil, (double)(TimeCurrent() + InpBasketPauseMinutes*60));
            ReleaseLock();
           }
         Log(StringFormat("СРАБОТАЛ ОБЩИЙ КОРЗИННЫЙ СТОП: плавающий убыток %.2f (%.2f%% equity). Пауза %d мин.",
                          floating, -floating/equity*100.0, InpBasketPauseMinutes));
         CloseAllRobotPositions("общий корзинный стоп");
        }
     }

   // 4. Корзинная фиксация прибыли: все позиции робота закрываются разом,
   //    когда суммарный плюс достиг цели. Паузы нет — можно входить снова.
   if(InpBasketTakeProfitPercent > 0 && floating > 0 &&
      floating >= equity * InpBasketTakeProfitPercent / 100.0)
     {
      Log(StringFormat("КОРЗИННАЯ ФИКСАЦИЯ ПРИБЫЛИ: +%.2f (%.2f%% equity)",
                       floating, floating/equity*100.0));
      CloseAllRobotPositions("корзинная фиксация прибыли");
     }

   // Дозакрытие остатков, если действует блокировка/пауза
   RetryCloseIfBlocked();
  }

// Разрешён ли вход по состоянию общего риска
bool SharedRiskAllowsEntry(string &reason)
  {
   long today = TodaySerial();
   if((long)GVGet(g_gvBlockedDay, -1) == today)
     {
      reason = "дневной лимит убытка достигнут — блокировка до следующего дня";
      return false;
     }
   datetime pauseUntil = (datetime)(long)GVGet(g_gvPauseUntil, 0);
   if(TimeCurrent() < pauseUntil)
     {
      reason = "пауза после корзинного стопа до " + TimeToString(pauseUntil, TIME_MINUTES);
      return false;
     }
   return true;
  }

//+------------------------------------------------------------------+
//| РАСЧЁТ ОБЪЁМА ОТ РИСКА (через OrderCalcProfit)                   |
//+------------------------------------------------------------------+
// Реальный денежный убыток 1.0 лота на дистанции entry -> sl.
// OrderCalcProfit корректно учитывает валюту прибыли и контракт
// конкретного брокера и для EURUSD, и для XAUUSD.
double LossPerLot(int dir, double entry, double sl)
  {
   double profit = 0;
   ENUM_ORDER_TYPE ot = (dir > 0) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(!OrderCalcProfit(ot, _Symbol, 1.0, entry, sl, profit))
      return 0;
   return MathAbs(profit);
  }

double CalcLotByRisk(int dir, double entry, double sl, double aiMult, string &reason)
  {
   double equity    = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskMoney = equity * ProfileRiskPercent() / 100.0 * aiMult;
   if(riskMoney <= 0)
     {
      reason = "нулевой бюджет риска (множитель ИИ = 0?)";
      return 0;
     }

   double lossPerLot = LossPerLot(dir, entry, sl);
   if(lossPerLot <= 0)
     {
      reason = "OrderCalcProfit не смог рассчитать убыток";
      return 0;
     }

   double lots = riskMoney / lossPerLot;

   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step > 0)
      lots = MathFloor(lots / step) * step;

   if(lots < minLot)
     {
      // Даже минимальный лот рискует больше бюджета — сделку не открываем
      if(lossPerLot * minLot > riskMoney)
        {
         reason = StringFormat("риск минимального лота %.2f превышает бюджет %.2f — вход отменён",
                               lossPerLot*minLot, riskMoney);
         return 0;
        }
      lots = minLot;
     }
   if(lots > maxLot)    lots = maxLot;
   if(lots > InpMaxLot) lots = InpMaxLot;

   // Проверка маржи
   double margin = 0;
   ENUM_ORDER_TYPE ot = (dir > 0) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(!OrderCalcMargin(ot, _Symbol, lots, entry, margin))
     {
      reason = "OrderCalcMargin вернул ошибку";
      return 0;
     }
   if(margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE) * 0.8)
     {
      reason = StringFormat("недостаточно свободной маржи: нужно %.2f, доступно %.2f",
                            margin, AccountInfoDouble(ACCOUNT_MARGIN_FREE));
      return 0;
     }

   return NormalizeDouble(lots, 2);
  }

// Оценка суммарного открытого риска позиций робота (дистанция до SL в деньгах)
double EstimateOpenRiskPercent()
  {
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity <= 0) return 100;
   double totalRisk = 0;
   int n = PositionsTotal();
   for(int i=0; i<n; i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsRobotPosition((long)PositionGetInteger(POSITION_MAGIC)))
         continue;
      string sym   = PositionGetString(POSITION_SYMBOL);
      double vol   = PositionGetDouble(POSITION_VOLUME);
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl    = PositionGetDouble(POSITION_SL);
      long   type  = PositionGetInteger(POSITION_TYPE);
      if(sl <= 0)
         continue; // у всех позиций робота SL есть; без SL риск не оцениваем
      double profit = 0;
      ENUM_ORDER_TYPE ot = (type == POSITION_TYPE_BUY) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      if(OrderCalcProfit(ot, sym, vol, entry, sl, profit))
         totalRisk += MathAbs(MathMin(profit, 0.0));
     }
   return totalRisk / equity * 100.0;
  }

//+------------------------------------------------------------------+
//| ОТКРЫТИЕ СДЕЛКИ                                                  |
//+------------------------------------------------------------------+
int CountMyPositions()
  {
   int count = 0;
   int n = PositionsTotal();
   for(int i=0; i<n; i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != g_magic)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      count++;
     }
   return count;
  }

// Ближайшая к текущей цене позиция того же направления — для правила
// минимального расстояния между входами
double NearestSameDirEntryDistance(int dir, double price)
  {
   double best = -1;
   int n = PositionsTotal();
   for(int i=0; i<n; i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != g_magic)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      long type = PositionGetInteger(POSITION_TYPE);
      if((dir > 0 && type != POSITION_TYPE_BUY) || (dir < 0 && type != POSITION_TYPE_SELL))
         continue;
      double d = MathAbs(price - PositionGetDouble(POSITION_PRICE_OPEN));
      if(best < 0 || d < best)
         best = d;
     }
   return best;
  }

void TryOpenTrade()
  {
   string reason = "";

   // --- 0. Профиль и общий риск
   if(!g_profileValid)                    { LogBlock(g_profileReason); return; }
   if(!SharedRiskAllowsEntry(reason))     { LogBlock(reason); return; }

   datetime gmt = GetGMT();

   // --- 1. Локальные фильтры (ИИ не может их обойти — они проверяются всегда)
   if(IsNearWeekClose(gmt, reason))       { LogBlock(reason); return; }
   if(g_profile == PROFILE_GOLD && InpGoldUseSessions)
     {
      if(!IsInGoldSession(gmt, reason))   { LogBlock(reason); return; }
     }
   if(IsNewsBlock(reason))                { LogBlock(reason); return; }
   if(IsSpreadTooWide(reason))            { LogBlock(reason); return; }

   // --- 2. Сигнал по закрытой свече
   string why = "";
   int dir = GetEntrySignal(why);
   if(dir == 0)                           { LogBlock(why); return; }
   if(!H1AllowsDirection(dir, reason))    { LogBlock(reason); return; }

   // --- 3. ИИ-ограничитель (только запрещает и уменьшает)
   bool aiAllowNew, aiAllowBuy, aiAllowSell;
   double aiMult;
   string aiWhy;
   GetAIGate(aiAllowNew, aiAllowBuy, aiAllowSell, aiMult, aiWhy);
   if(!aiAllowNew)                        { LogBlock(aiWhy); return; }
   if(dir > 0 && !aiAllowBuy)             { LogBlock(aiWhy); return; }
   if(dir < 0 && !aiAllowSell)            { LogBlock(aiWhy); return; }

   // --- 4. Лимиты количества и портфельного риска
   if(CountMyPositions() >= InpMaxPositionsPerSymbol)
     {
      LogBlock("достигнут лимит позиций на инструмент (" + IntegerToString(InpMaxPositionsPerSymbol) + ")");
      return;
     }
   double openRisk = EstimateOpenRiskPercent();
   if(openRisk + ProfileRiskPercent() > InpMaxPortfolioRiskPercent)
     {
      LogBlock(StringFormat("портфельный риск %.2f%% + новый %.2f%% > лимита %.2f%%",
                            openRisk, ProfileRiskPercent(), InpMaxPortfolioRiskPercent));
      return;
     }

   // --- 5. Цены, SL/TP только от ATR
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double entry = (dir > 0) ? ask : bid;

   // Минимальное расстояние между входами одного направления
   double nearest = NearestSameDirEntryDistance(dir, entry);
   if(nearest >= 0 && nearest < g_atr * InpMinAddDistanceATR)
     {
      LogBlock("слишком близко к существующей позиции (защита от наслаивания)");
      return;
     }

   double slDist = g_atr * ProfileSLatr();
   double tpDist = g_atr * ProfileTPatr();

   // Учёт stop level брокера: SL/TP не могут быть ближе разрешённого
   double stopsLevel = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   double minDist = stopsLevel + (ask - bid);
   if(slDist < minDist) slDist = minDist * 1.1;
   if(tpDist < minDist) tpDist = minDist * 1.1;

   double sl = (dir > 0) ? entry - slDist : entry + slDist;
   double tp = (dir > 0) ? entry + tpDist : entry - tpDist;
   sl = NormalizeDouble(sl, _Digits);
   tp = NormalizeDouble(tp, _Digits);

   // --- 6. Объём от риска и фактической дистанции SL
   double lots = CalcLotByRisk(dir, entry, sl, aiMult, reason);
   if(lots <= 0)                          { LogBlock(reason); return; }

   // --- 7. Открытие
   bool ok;
   string comment = "DG " + g_canonSymbol;
   if(dir > 0)
      ok = g_trade.Buy(lots, _Symbol, 0, sl, tp, comment);
   else
      ok = g_trade.Sell(lots, _Symbol, 0, sl, tp, comment);

   if(ok)
     {
      g_lastEntryBarTime = g_lastBarTime;
      g_lastBlockReason = "";
      Log(StringFormat("ОТКРЫТА СДЕЛКА %s %.2f лот, SL=%s TP=%s | сигнал: %s | ИИ: %s (множитель %.2f)",
                       (dir>0?"BUY":"SELL"), lots,
                       DoubleToString(sl,_Digits), DoubleToString(tp,_Digits),
                       why, aiWhy, aiMult));
     }
   else
      Log("Ошибка открытия сделки: retcode=" + IntegerToString((int)g_trade.ResultRetcode()) +
          " (" + g_trade.ResultRetcodeDescription() + ")");
  }

//+------------------------------------------------------------------+
//| СОПРОВОЖДЕНИЕ ПОЗИЦИЙ (каждый тик): безубыток и трейлинг         |
//| Правило: SL можно только подтягивать ближе, никогда не отодвигать|
//+------------------------------------------------------------------+
void ManageOpenPositions()
  {
   if(!g_indReady || g_atr <= 0)
      return;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double freezeLevel = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL) * _Point;
   double stopsLevel  = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   double spread = ask - bid;

   double trailDist = g_atr * ProfileTrailATR();
   double beTrigger = g_atr * ProfileBEatr();

   int n = PositionsTotal();
   for(int i=0; i<n; i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != g_magic)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;

      long   type   = PositionGetInteger(POSITION_TYPE);
      double entry  = PositionGetDouble(POSITION_PRICE_OPEN);
      double curSL  = PositionGetDouble(POSITION_SL);
      double curTP  = PositionGetDouble(POSITION_TP);
      double newSL  = curSL;

      if(type == POSITION_TYPE_BUY)
        {
         // Безубыток: цена прошла beTrigger — SL на вход (+ компенсация спреда)
         if(bid - entry >= beTrigger)
           {
            double bePrice = NormalizeDouble(entry + spread, _Digits);
            if(bePrice > newSL) newSL = bePrice;
           }
         // Трейлинг только по прибыльной позиции
         if(bid - entry > trailDist)
           {
            double trailSL = NormalizeDouble(bid - trailDist, _Digits);
            if(trailSL > newSL) newSL = trailSL;
           }
         // Только подтягиваем (newSL выше старого), с учётом stop/freeze level
         if(newSL > curSL + _Point &&
            bid - newSL > MathMax(stopsLevel, freezeLevel))
           {
            if(!g_trade.PositionModify(ticket, newSL, curTP) && InpVerboseLog)
               Log("Не удалось изменить SL #" + IntegerToString((long)ticket));
           }
        }
      else // SELL: зеркально, SL только вниз (ближе к цене)
        {
         double candidate = 0; // лучший (самый низкий) новый SL

         if(entry - ask >= beTrigger)
            candidate = NormalizeDouble(entry - spread, _Digits);

         if(entry - ask > trailDist)
           {
            double trailSL = NormalizeDouble(ask + trailDist, _Digits);
            if(candidate <= 0 || trailSL < candidate)
               candidate = trailSL;
           }

         bool better = (candidate > 0) && (curSL <= 0 || candidate < curSL - _Point);
         if(better && candidate - ask > MathMax(stopsLevel, freezeLevel))
           {
            if(!g_trade.PositionModify(ticket, candidate, curTP) && InpVerboseLog)
               Log("Не удалось изменить SL #" + IntegerToString((long)ticket));
           }
        }
     }
  }

//+------------------------------------------------------------------+
//| ПАНЕЛЬ СОСТОЯНИЯ НА ГРАФИКЕ                                      |
//+------------------------------------------------------------------+
void UpdateChartComment()
  {
   string ai;
   if(!g_aiEnabled)
      ai = "выключен";
   else if(g_aiLastOkTime == 0)
      ai = "мост не отвечал (" + (InpBridgeFailureMode==BRIDGE_PAUSE ? "pause" : "baseline") + ")";
   else
     {
      int age = (int)((TimeCurrent() - g_aiLastOkTime) / 60);
      ai = EnumToString(g_aiRegime) + ", кэш " + IntegerToString(age) + " мин" +
           (age >= InpAICacheTTLMinutes ? " (УСТАРЕЛ -> chaos)" : "");
     }

   long today = TodaySerial();
   string riskState = "норма";
   if((long)GVGet(g_gvBlockedDay, -1) == today)
      riskState = "ДНЕВНОЙ ЛИМИТ — торговля остановлена";
   else if(TimeCurrent() < (datetime)(long)GVGet(g_gvPauseUntil, 0))
      riskState = "пауза после корзинного стопа";

   string text =
      "DualGuard EA | " + g_canonSymbol + " | magic " + IntegerToString(g_magic) + "\n" +
      (g_profileValid ? "" : ("ПРОФИЛЬ НЕ АКТИВЕН: " + g_profileReason + "\n")) +
      "Общий риск: " + riskState +
      " | баланс дня: " + DoubleToString(GVGet(g_gvDayBalance,0), 2) +
      " | плавающий P/L робота: " + DoubleToString(RobotFloatingPL(), 2) + "\n" +
      "ИИ: " + ai + "\n" +
      "Последняя причина блокировки: " + (g_lastBlockReason=="" ? "-" : g_lastBlockReason);
   Comment(text);
  }

//+------------------------------------------------------------------+
//| ОБРАБОТЧИКИ СОБЫТИЙ                                              |
//+------------------------------------------------------------------+
int OnInit()
  {
   InitProfile();
   if(!g_profileValid)
      Log("ВНИМАНИЕ: " + g_profileReason);
   else
      Log("Профиль: " + (g_profile==PROFILE_GOLD ? "gold" : "eurusd") +
          ", magic=" + IntegerToString(g_magic) +
          ", magic партнёра=" + IntegerToString(g_partnerMagic));

   if(_Period != PERIOD_M5)
      Log("ВНИМАНИЕ: советник рассчитан на график M5, текущий период: " + EnumToString(_Period));

   g_trade.SetExpertMagicNumber(g_magic);
   g_trade.SetDeviationInPoints(20);

   // Хэндлы индикаторов создаются один раз — это критично для скорости
   g_hEmaFast = iMA(_Symbol, PERIOD_M5, InpEMAFastPeriod, 0, MODE_EMA, PRICE_CLOSE);
   g_hEmaSlow = iMA(_Symbol, PERIOD_M5, InpEMASlowPeriod, 0, MODE_EMA, PRICE_CLOSE);
   g_hRsi     = iRSI(_Symbol, PERIOD_M5, InpRSIPeriod, PRICE_CLOSE);
   g_hAtr     = iATR(_Symbol, PERIOD_M5, InpATRPeriod);
   if(InpUseH1TrendFilter)
     {
      g_hH1Fast = iMA(_Symbol, PERIOD_H1, InpH1EMAFast, 0, MODE_EMA, PRICE_CLOSE);
      g_hH1Slow = iMA(_Symbol, PERIOD_H1, InpH1EMASlow, 0, MODE_EMA, PRICE_CLOSE);
     }
   if(InpEntryMode != ENTRY_TREND)
      g_hBB = iBands(_Symbol, PERIOD_M5, InpBBPeriod, 0, InpBBDeviation, PRICE_CLOSE);
   if(g_hEmaFast==INVALID_HANDLE || g_hEmaSlow==INVALID_HANDLE ||
      g_hRsi==INVALID_HANDLE || g_hAtr==INVALID_HANDLE ||
      (InpEntryMode != ENTRY_TREND && g_hBB==INVALID_HANDLE) ||
      (InpUseH1TrendFilter && (g_hH1Fast==INVALID_HANDLE || g_hH1Slow==INVALID_HANDLE)))
     {
      Log("ОШИБКА: не удалось создать хэндлы индикаторов");
      return INIT_FAILED;
     }

   InitSharedRiskNames();

   // ИИ: в тестере WebRequest запрещён — мост автоматически отключается
   g_aiEnabled = InpEnableAI;
   if(g_aiEnabled && (bool)MQLInfoInteger(MQL_TESTER))
     {
      g_aiEnabled = false;
      Log("Тестер стратегий: ИИ-мост отключён автоматически (WebRequest недоступен). " +
          "Тестируется локальная логика.");
     }

   g_aiNextPollTime  = 0; // первый опрос — сразу
   g_newsNextRefresh = 0;

   EventSetTimer(15);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   Comment("");
   if(g_hEmaFast!=INVALID_HANDLE) IndicatorRelease(g_hEmaFast);
   if(g_hEmaSlow!=INVALID_HANDLE) IndicatorRelease(g_hEmaSlow);
   if(g_hRsi!=INVALID_HANDLE)     IndicatorRelease(g_hRsi);
   if(g_hAtr!=INVALID_HANDLE)     IndicatorRelease(g_hAtr);
   if(g_hH1Fast!=INVALID_HANDLE)  IndicatorRelease(g_hH1Fast);
   if(g_hH1Slow!=INVALID_HANDLE)  IndicatorRelease(g_hH1Slow);
   if(g_hBB!=INVALID_HANDLE)      IndicatorRelease(g_hBB);
  }

void OnTimer()
  {
   // Опрос моста раз в InpBridgePollMinutes
   if(g_aiEnabled && TimeCurrent() >= g_aiNextPollTime)
     {
      g_aiNextPollTime = TimeCurrent() + InpBridgePollMinutes*60;
      PollBridge();
     }
   // Обновление кэша новостей раз в 15 минут
   if(TimeCurrent() >= g_newsNextRefresh)
     {
      g_newsNextRefresh = TimeCurrent() + 15*60;
      RefreshNewsCache();
     }
   UpdateChartComment();
  }

void OnTick()
  {
   // Сопровождение позиций и общий риск — на каждом тике
   if(g_indReady)
     {
      ManageOpenPositions();
      CheckSharedRisk();
     }

   // Оценка входа — только по закрытой свече M5
   datetime barTime = iTime(_Symbol, PERIOD_M5, 0);
   if(barTime == g_lastBarTime)
      return;
   g_lastBarTime = barTime;

   if(!UpdateIndicatorCache())
      return;

   // В тестере таймер может не тикать между барами — обновим новости здесь
   if((bool)MQLInfoInteger(MQL_TESTER) && TimeCurrent() >= g_newsNextRefresh)
     {
      g_newsNextRefresh = TimeCurrent() + 15*60;
      RefreshNewsCache();
     }

   // Не больше одного входа на свечу
   if(g_lastEntryBarTime == barTime)
      return;

   TryOpenTrade();
  }
//+------------------------------------------------------------------+
