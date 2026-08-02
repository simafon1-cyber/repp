// Мини-эмуляция функций MQL5 для запуска «чистой» логики советника в C++.
// Поведение повторяет документацию MQL5: datetime — секунды с 1970 года
// без часового пояса, StringSubstr/StringFind как в MQL5.
#pragma once

// Все стандартные заголовки подключаются ДО псевдонима `string`,
// иначе он сломает разбор самой стандартной библиотеки.
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <iostream>
#include <map>
#include <string>
#include <vector>

typedef long long datetime;
typedef unsigned short ushort;

struct MqlDateTime {
    int year, mon, day, hour, min, sec, day_of_week, day_of_year;
};

inline void TimeToStruct(datetime t, MqlDateTime &st) {
    time_t tt = (time_t)t;
    struct tm g;
    gmtime_r(&tt, &g);
    st.year = g.tm_year + 1900;
    st.mon = g.tm_mon + 1;
    st.day = g.tm_mday;
    st.hour = g.tm_hour;
    st.min = g.tm_min;
    st.sec = g.tm_sec;
    st.day_of_week = g.tm_wday;  // 0 = воскресенье, как в MQL5
    st.day_of_year = g.tm_yday;
}

inline datetime StructToTime(MqlDateTime &st) {
    struct tm g = {};
    g.tm_year = st.year - 1900;
    g.tm_mon = st.mon - 1;
    g.tm_mday = st.day;
    g.tm_hour = st.hour;
    g.tm_min = st.min;
    g.tm_sec = st.sec;
    return (datetime)timegm(&g);
}

// --- Строковые функции MQL5 ---
inline int StringLen(const std::string &s) { return (int)s.size(); }

inline int StringFind(const std::string &s, const std::string &sub, int start = 0) {
    if (start < 0 || start > (int)s.size()) return -1;
    size_t p = s.find(sub, (size_t)start);
    return p == std::string::npos ? -1 : (int)p;
}

inline std::string StringSubstr(const std::string &s, int start, int count = -1) {
    if (start < 0 || start >= (int)s.size()) return "";
    if (count < 0) return s.substr((size_t)start);
    return s.substr((size_t)start, (size_t)count);
}

inline ushort StringGetCharacter(const std::string &s, int pos) {
    if (pos < 0 || pos >= (int)s.size()) return 0;
    return (ushort)(unsigned char)s[(size_t)pos];
}

// Динамический массив MQL5 (`string parts[];`). Экстрактор превращает такие
// объявления в MqlArray<T>, потому что в C++ такого синтаксиса нет.
template <typename T>
struct MqlArray {
    std::vector<T> data;
    T &operator[](int i) { return data[(size_t)i]; }
    const T &operator[](int i) const { return data[(size_t)i]; }
};

template <typename T>
inline int ArraySize(const MqlArray<T> &a) {
    return (int)a.data.size();
}

template <typename T>
inline int ArrayResize(MqlArray<T> &a, int n) {
    a.data.resize((size_t)n);
    return n;
}

inline int StringSplit(const std::string &s, ushort sep, MqlArray<std::string> &out) {
    out.data.clear();
    std::string cur;
    for (char c : s) {
        if ((ushort)(unsigned char)c == sep) {
            out.data.push_back(cur);
            cur.clear();
        } else {
            cur += c;
        }
    }
    out.data.push_back(cur);
    return (int)out.data.size();
}

inline long long StringToInteger(const std::string &s) { return atoll(s.c_str()); }
inline double StringToDouble(const std::string &s) { return atof(s.c_str()); }

inline std::string IntegerToString(long long v) { return std::to_string(v); }

// В MQL5 литералы имеют тип `string`; экстрактор оборачивает их в mqlstr().
inline std::string mqlstr(const char *s) { return std::string(s); }

// std::string нельзя передавать в printf — подменяем на c_str()
template <typename T>
inline T fmt_arg(T v) {
    return v;
}
inline const char *fmt_arg(const std::string &s) { return s.c_str(); }

template <typename... Args>
inline std::string StringFormat(const std::string &fmt, Args... args) {
    char buf[4096];
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wformat-security"
    snprintf(buf, sizeof(buf), fmt.c_str(), fmt_arg(args)...);
#pragma GCC diagnostic pop
    return std::string(buf);
}

// Печать в журнал советника — в тестах молчит, чтобы не засорять вывод
extern bool g_shim_verbose;
template <typename... Args>
inline void PrintFormat(const std::string &fmt, Args... args) {
    if (g_shim_verbose) std::cout << "    [лог] " << StringFormat(fmt, args...) << "\n";
}
inline void Print(const std::string &msg) {
    if (g_shim_verbose) std::cout << "    [лог] " << msg << "\n";
}

inline std::string TimeToString(datetime t, int mode = 0) {
    (void)mode;
    MqlDateTime st;
    TimeToStruct(t, st);
    return StringFormat("%02d:%02d", st.hour, st.min);
}

// --- Математика ---
inline double MathMax(double a, double b) { return a > b ? a : b; }
inline double MathMin(double a, double b) { return a < b ? a : b; }
inline int MathMax(int a, int b) { return a > b ? a : b; }
inline int MathMin(int a, int b) { return a < b ? a : b; }
inline double MathAbs(double a) { return std::fabs(a); }
inline double MathFloor(double a) { return std::floor(a); }
inline double MathRound(double a) { return std::round(a); }
inline double MathPow(double a, double b) { return std::pow(a, b); }

// --- Типы MQL5, используемые в извлечённых функциях ---
typedef std::string string;  // в MQL5 тип называется просто `string`
#define TIME_MINUTES 1
#define TIME_DATE 2
#define TIME_SECONDS 4

enum ENUM_AI_REGIME {
    REGIME_NONE = 0,
    REGIME_TREND_UP,
    REGIME_TREND_DOWN,
    REGIME_RANGE,
    REGIME_CHAOS
};

enum ENUM_PROFILE_MODE { PROFILE_AUTO = 0, PROFILE_EURUSD = 1, PROFILE_GOLD = 2 };
enum ENUM_BRIDGE_FAILURE { BRIDGE_PAUSE = 0, BRIDGE_BASELINE = 1 };
enum ENUM_ORDER_TYPE { ORDER_TYPE_BUY = 0, ORDER_TYPE_SELL = 1 };
enum ENUM_ACCOUNT_INFO_DOUBLE { ACCOUNT_EQUITY = 0, ACCOUNT_MARGIN_FREE = 1 };
enum ENUM_SYMBOL_INFO_DOUBLE { SYMBOL_VOLUME_MIN = 0, SYMBOL_VOLUME_MAX, SYMBOL_VOLUME_STEP };

// --- Управляемое «состояние терминала» для тестов ---
struct FakeTerminal {
    double equity = 10000.0;
    double free_margin = 10000.0;
    double volume_min = 0.01;
    double volume_max = 100.0;
    double volume_step = 0.01;
    // Размер контракта: 100 000 для EURUSD, 100 для XAUUSD — как у брокеров
    double contract_size = 100000.0;
    double margin_per_lot = 300.0;
    bool calc_profit_fails = false;
    datetime now = 0;
    long login = 123456;
};
extern FakeTerminal g_fake;

inline double AccountInfoDouble(ENUM_ACCOUNT_INFO_DOUBLE what) {
    return what == ACCOUNT_EQUITY ? g_fake.equity : g_fake.free_margin;
}

inline double SymbolInfoDouble(const string &, ENUM_SYMBOL_INFO_DOUBLE what) {
    if (what == SYMBOL_VOLUME_MIN) return g_fake.volume_min;
    if (what == SYMBOL_VOLUME_MAX) return g_fake.volume_max;
    return g_fake.volume_step;
}

// Повторяет поведение MQL5 для инструментов, котируемых в USD:
// прибыль = (цена_закрытия - цена_открытия) * объём * размер_контракта
inline bool OrderCalcProfit(ENUM_ORDER_TYPE type, const string &, double volume,
                            double open, double close, double &profit) {
    if (g_fake.calc_profit_fails) return false;
    double diff = (type == ORDER_TYPE_BUY) ? (close - open) : (open - close);
    profit = diff * volume * g_fake.contract_size;
    return true;
}

inline bool OrderCalcMargin(ENUM_ORDER_TYPE, const string &, double volume,
                            double, double &margin) {
    margin = volume * g_fake.margin_per_lot;
    return true;
}

inline double NormalizeDouble(double value, int digits) {
    double p = std::pow(10.0, digits);
    return std::round(value * p) / p;
}

inline datetime TimeCurrent() { return g_fake.now; }
inline datetime TimeTradeServer() { return g_fake.now; }

// --- Глобальные переменные терминала MT5 ---
// В MT5 они переживают перезапуск советника и самого терминала; здесь —
// обычная таблица, которую тест может очистить, имитируя новый запуск.
extern std::map<std::string, double> g_globals;

inline bool GlobalVariableCheck(const std::string &name) {
    return g_globals.find(name) != g_globals.end();
}
inline double GlobalVariableGet(const std::string &name) {
    auto it = g_globals.find(name);
    return it == g_globals.end() ? 0.0 : it->second;
}
inline datetime GlobalVariableSet(const std::string &name, double value) {
    g_globals[name] = value;
    return g_fake.now;
}

enum ENUM_ACCOUNT_INFO_INTEGER { ACCOUNT_LOGIN = 0 };
inline long AccountInfoInteger(ENUM_ACCOUNT_INFO_INTEGER) { return g_fake.login; }

extern string _Symbol;

// Входные параметры советника (в тестах — изменяемые)
extern string InpGoldSessionStartLondon;
extern string InpGoldSessionEndNewYork;
extern int InpMinutesBeforeWeekClose;
extern double InpRiskPercentEURUSD;
extern double InpRiskPercentGold;
extern double InpMaxLot;
extern int InpAICacheTTLMinutes;
extern ENUM_BRIDGE_FAILURE InpBridgeFailureMode;

// Состояние советника, от которого зависят проверяемые функции
extern ENUM_PROFILE_MODE g_profile;
extern bool g_aiEnabled;
extern datetime g_aiLastOkTime;
extern ENUM_AI_REGIME g_aiRegime;
extern bool g_aiTradeAllowed;
extern double g_aiRiskMultiplier;
extern string g_aiReason;
