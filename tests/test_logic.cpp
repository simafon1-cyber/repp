// Тесты «чистой» логики советника DualGuard EA.
// Функции берутся напрямую из mql5/DualGuardEA.mq5 (см. extract_functions.py),
// поэтому тесты проверяют настоящий код, а не его копию.

#include "mql5_shim.h"


string InpGoldSessionStartLondon = "08:00";
string InpGoldSessionEndNewYork = "17:00";
int InpMinutesBeforeWeekClose = 40;
double InpRiskPercentEURUSD = 0.5;
double InpRiskPercentGold = 0.3;
double InpMaxLot = 1.0;
int InpAICacheTTLMinutes = 45;
ENUM_BRIDGE_FAILURE InpBridgeFailureMode = BRIDGE_PAUSE;

string _Symbol = "EURUSD";
FakeTerminal g_fake;

ENUM_PROFILE_MODE g_profile = PROFILE_EURUSD;
bool g_aiEnabled = true;
datetime g_aiLastOkTime = 0;
ENUM_AI_REGIME g_aiRegime = REGIME_NONE;
bool g_aiTradeAllowed = true;
double g_aiRiskMultiplier = 1.0;
string g_aiReason = "";

#include "generated_functions.h"

static int g_failed = 0;
static int g_passed = 0;

static void check(bool ok, const std::string &name, const std::string &detail = "") {
    if (ok) {
        ++g_passed;
        std::cout << "  OK   " << name << "\n";
    } else {
        ++g_failed;
        std::cout << "  СБОЙ " << name << (detail.empty() ? "" : "  -> " + detail) << "\n";
    }
}

// Удобная сборка момента времени UTC
static datetime utc(int y, int mo, int d, int h, int mi) {
    MqlDateTime st = {};
    st.year = y; st.mon = mo; st.day = d; st.hour = h; st.min = mi; st.sec = 0;
    return StructToTime(st);
}

int main() {
    std::cout << "\n=== 1. Нормализация минут (окно через полночь) ===\n";
    check(NormalizeMinutes(0) == 0, "0 -> 0");
    check(NormalizeMinutes(1500) == 60, "1500 -> 60 (следующие сутки)");
    check(NormalizeMinutes(-60) == 1380, "-60 -> 1380 (предыдущие сутки)");
    check(NormalizeMinutes(1439) == 1439, "1439 -> 1439");

    std::cout << "\n=== 2. Разбор времени HH:MM ===\n";
    check(ParseHHMM("08:00") == 480, "08:00 -> 480");
    check(ParseHHMM("17:30") == 1050, "17:30 -> 1050");
    check(ParseHHMM("25:00") == -1, "25:00 -> ошибка");
    check(ParseHHMM("abc") == -1, "мусор -> ошибка");
    check(ParseHHMM("08") == -1, "без двоеточия -> ошибка");

    std::cout << "\n=== 3. Летнее время Европы и США (2026 год) ===\n";
    // ЕС: летнее время с 29 марта по 25 октября 2026
    check(!IsEUDst(utc(2026, 3, 29, 0, 30)), "ЕС: 29 марта 00:30 UTC — ещё зима");
    check(IsEUDst(utc(2026, 3, 29, 1, 30)), "ЕС: 29 марта 01:30 UTC — уже лето");
    check(IsEUDst(utc(2026, 7, 15, 12, 0)), "ЕС: июль — лето");
    check(!IsEUDst(utc(2026, 12, 15, 12, 0)), "ЕС: декабрь — зима");
    check(!IsEUDst(utc(2026, 10, 25, 2, 0)), "ЕС: 25 октября 02:00 UTC — снова зима");
    // США: летнее время с 8 марта по 1 ноября 2026
    check(IsUSDst(utc(2026, 3, 8, 8, 0)), "США: 8 марта 08:00 UTC — лето");
    check(!IsUSDst(utc(2026, 3, 8, 6, 0)), "США: 8 марта 06:00 UTC — ещё зима");
    check(IsUSDst(utc(2026, 7, 15, 12, 0)), "США: июль — лето");
    check(!IsUSDst(utc(2026, 11, 1, 7, 0)), "США: 1 ноября 07:00 UTC — снова зима");
    check(!IsUSDst(utc(2026, 1, 15, 12, 0)), "США: январь — зима");

    std::cout << "\n=== 4. Сессия золота: Лондон 08:00 - Нью-Йорк 17:00 ===\n";
    {
        std::string reason;
        // ЗИМА: Лондон = GMT, Нью-Йорк = GMT-5 -> окно 08:00..22:00 GMT
        check(!IsInGoldSession(utc(2026, 1, 15, 7, 30), reason), "зима: 07:30 GMT — до открытия", reason);
        check(IsInGoldSession(utc(2026, 1, 15, 8, 0), reason), "зима: 08:00 GMT — открытие Лондона", reason);
        check(IsInGoldSession(utc(2026, 1, 15, 14, 0), reason), "зима: 14:00 GMT — середина", reason);
        check(IsInGoldSession(utc(2026, 1, 15, 21, 59), reason), "зима: 21:59 GMT — ещё торгуем", reason);
        check(!IsInGoldSession(utc(2026, 1, 15, 22, 0), reason), "зима: 22:00 GMT — закрытие Нью-Йорка", reason);
        check(!IsInGoldSession(utc(2026, 1, 15, 2, 0), reason), "зима: 02:00 GMT — азиатская сессия отключена", reason);

        // ЛЕТО: Лондон = GMT+1, Нью-Йорк = GMT-4 -> окно 07:00..21:00 GMT
        check(!IsInGoldSession(utc(2026, 7, 15, 6, 30), reason), "лето: 06:30 GMT — до открытия", reason);
        check(IsInGoldSession(utc(2026, 7, 15, 7, 0), reason), "лето: 07:00 GMT — открытие Лондона", reason);
        check(IsInGoldSession(utc(2026, 7, 15, 20, 59), reason), "лето: 20:59 GMT — ещё торгуем", reason);
        check(!IsInGoldSession(utc(2026, 7, 15, 21, 0), reason), "лето: 21:00 GMT — закрытие Нью-Йорка", reason);
        check(!IsInGoldSession(utc(2026, 7, 15, 3, 0), reason), "лето: 03:00 GMT — азиатская сессия отключена", reason);
    }

    std::cout << "\n=== 5. Сессия с переходом через полночь (проверка исправления) ===\n";
    {
        std::string reason;
        InpGoldSessionEndNewYork = "20:00";  // 20:00 Нью-Йорка = 01:00 GMT следующих суток (зима)
        check(IsInGoldSession(utc(2026, 1, 15, 23, 0), reason), "окно через полночь: 23:00 GMT внутри", reason);
        check(IsInGoldSession(utc(2026, 1, 15, 0, 30), reason), "окно через полночь: 00:30 GMT внутри", reason);
        check(!IsInGoldSession(utc(2026, 1, 15, 5, 0), reason), "окно через полночь: 05:00 GMT снаружи", reason);
        InpGoldSessionEndNewYork = "17:00";  // возвращаем значение по умолчанию
    }

    std::cout << "\n=== 6. Фильтр закрытия недели ===\n";
    {
        std::string reason;
        // 16 января 2026 — пятница. Зимой закрытие в 22:00 GMT, буфер 40 минут.
        check(!IsNearWeekClose(utc(2026, 1, 16, 21, 0), reason), "пятница 21:00 GMT — ещё можно");
        check(IsNearWeekClose(utc(2026, 1, 16, 21, 30), reason), "пятница 21:30 GMT — входы закрыты");
        check(!IsNearWeekClose(utc(2026, 1, 15, 23, 0), reason), "четверг 23:00 GMT — фильтр не действует");
        // 17 июля 2026 — пятница, лето: закрытие 21:00 GMT
        check(IsNearWeekClose(utc(2026, 7, 17, 20, 30), reason), "лето, пятница 20:30 GMT — входы закрыты");
        check(!IsNearWeekClose(utc(2026, 7, 17, 20, 0), reason), "лето, пятница 20:00 GMT — ещё можно");
    }

    std::cout << "\n=== 7. Разбор JSON от моста ===\n";
    {
        std::string out;
        std::string json = "{\"regime\": \"trend_up\", \"trade_allowed\": true, "
                           "\"risk_multiplier\": 0.5, \"reason\": \"тест\"}";
        check(JsonGetRaw(json, "regime", out) && out == "trend_up", "строковое поле", out);
        check(JsonGetRaw(json, "trade_allowed", out) && out == "true", "булево поле", out);
        check(JsonGetRaw(json, "risk_multiplier", out) && out == "0.5", "числовое поле", out);
        check(JsonGetRaw(json, "reason", out) && out == "тест", "поле с кириллицей", out);
        check(!JsonGetRaw(json, "missing", out), "отсутствующее поле -> false");
    }

    std::cout << "\n=== 8. Проверка ответа моста (только допустимые значения) ===\n";
    {
        ENUM_AI_REGIME regime;
        bool allowed;
        double mult;
        std::string reason;

        auto parse = [&](const std::string &j) {
            regime = REGIME_NONE; allowed = true; mult = -1; reason = "";
            return ParseBridgeResponse(j, regime, allowed, mult, reason);
        };

        check(parse("{\"regime\":\"chaos\",\"trade_allowed\":false,\"risk_multiplier\":0.0,\"reason\":\"x\"}")
                  && regime == REGIME_CHAOS && !allowed && mult == 0.0,
              "корректный ответ chaos принят");
        check(parse("{\"regime\":\"trend_down\",\"trade_allowed\":true,\"risk_multiplier\":1.0,\"reason\":\"x\"}")
                  && regime == REGIME_TREND_DOWN && allowed && mult == 1.0,
              "корректный ответ trend_down принят");
        check(!parse("{\"regime\":\"sideways\",\"trade_allowed\":true,\"risk_multiplier\":0.5,\"reason\":\"x\"}"),
              "неизвестный режим отклонён");
        check(!parse("{\"regime\":\"range\",\"trade_allowed\":yes,\"risk_multiplier\":0.5,\"reason\":\"x\"}"),
              "нечисловой trade_allowed отклонён");
        check(!parse("{\"regime\":\"range\",\"trade_allowed\":true,\"risk_multiplier\":1.5,\"reason\":\"x\"}"),
              "risk_multiplier > 1 отклонён (ИИ не может увеличить риск)");
        check(!parse("{\"regime\":\"range\",\"trade_allowed\":true,\"risk_multiplier\":-0.5,\"reason\":\"x\"}"),
              "отрицательный risk_multiplier отклонён");
        check(!parse("{\"regime\":\"range\",\"trade_allowed\":true,\"reason\":\"x\"}"),
              "ответ без risk_multiplier отклонён");
        check(!parse("это вообще не json"), "мусор отклонён");
        check(parse("{\"regime\":\"range\",\"trade_allowed\":true,\"risk_multiplier\":0.25}")
                  && mult == 0.25,
              "ответ без reason принят (reason необязателен)");
    }

    std::cout << "\n=== 9. Расчёт лота от риска: EURUSD ===\n";
    {
        // Счёт 10 000, риск 0.5% = 50 долларов. SL на 20 пунктов (0.0020).
        // Убыток 1 лота = 0.0020 * 100000 = 200 долларов -> лот = 50/200 = 0.25
        g_profile = PROFILE_EURUSD;
        _Symbol = "EURUSD";
        g_fake = FakeTerminal();
        g_fake.contract_size = 100000.0;
        std::string reason;

        double lot = CalcLotByRisk(1, 1.1000, 1.0980, 1.0, reason);
        check(std::fabs(lot - 0.25) < 1e-9, "риск 0.5% от 10000, SL 20 пт -> 0.25 лота",
              "получено " + std::to_string(lot) + " " + reason);

        // Множитель ИИ 0.5 обязан РОВНО вдвое уменьшить объём
        lot = CalcLotByRisk(1, 1.1000, 1.0980, 0.5, reason);
        check(std::fabs(lot - 0.12) < 1e-9, "множитель ИИ 0.5 уменьшает лот (0.25 -> 0.12 с шагом 0.01)",
              "получено " + std::to_string(lot));

        // Множитель ИИ 0 -> сделки нет вообще
        lot = CalcLotByRisk(1, 1.1000, 1.0980, 0.0, reason);
        check(lot == 0, "множитель ИИ 0.0 -> вход отменён", reason);

        // Продажа считается симметрично
        lot = CalcLotByRisk(-1, 1.1000, 1.1020, 1.0, reason);
        check(std::fabs(lot - 0.25) < 1e-9, "SELL считается симметрично BUY",
              "получено " + std::to_string(lot));
    }

    std::cout << "\n=== 10. Расчёт лота от риска: XAUUSD (другой контракт!) ===\n";
    {
        // Золото: контракт 100 унций, риск 0.3% от 10000 = 30 долларов.
        // SL 5 долларов -> убыток 1 лота = 5 * 100 = 500 -> лот = 30/500 = 0.06
        g_profile = PROFILE_GOLD;
        _Symbol = "XAUUSD";
        g_fake = FakeTerminal();
        g_fake.contract_size = 100.0;
        std::string reason;

        double lot = CalcLotByRisk(1, 2400.0, 2395.0, 1.0, reason);
        check(std::fabs(lot - 0.06) < 1e-9, "риск 0.3% от 10000, SL 5$ -> 0.06 лота",
              "получено " + std::to_string(lot) + " " + reason);

        // Тот же процент риска, но вдвое дальше стоп -> вдвое меньше объём
        lot = CalcLotByRisk(1, 2400.0, 2390.0, 1.0, reason);
        check(std::fabs(lot - 0.03) < 1e-9, "стоп вдвое дальше -> лот вдвое меньше",
              "получено " + std::to_string(lot));
    }

    std::cout << "\n=== 11. Защита при расчёте лота ===\n";
    {
        g_profile = PROFILE_EURUSD;
        _Symbol = "EURUSD";
        std::string reason;

        // Минимальный лот рискует больше бюджета -> вход обязан отмениться
        g_fake = FakeTerminal();
        g_fake.equity = 100.0;          // риск 0.5% = 0.50 доллара
        g_fake.contract_size = 100000.0;
        double lot = CalcLotByRisk(1, 1.1000, 1.0900, 1.0, reason);  // SL 100 пт = 1000$/лот
        check(lot == 0, "риск минимального лота больше бюджета -> вход отменён", reason);

        // Потолок лота InpMaxLot соблюдается
        g_fake = FakeTerminal();
        g_fake.equity = 1000000.0;
        g_fake.free_margin = 1000000.0;
        g_fake.contract_size = 100000.0;
        InpMaxLot = 1.0;
        lot = CalcLotByRisk(1, 1.1000, 1.0980, 1.0, reason);
        check(lot == 1.0, "лот ограничен потолком InpMaxLot", "получено " + std::to_string(lot));

        // Нехватка маржи -> вход отменяется
        g_fake = FakeTerminal();
        g_fake.equity = 10000.0;
        g_fake.free_margin = 10.0;
        g_fake.contract_size = 100000.0;
        lot = CalcLotByRisk(1, 1.1000, 1.0980, 1.0, reason);
        check(lot == 0, "недостаточно свободной маржи -> вход отменён", reason);

        // Если OrderCalcProfit не сработал — сделки быть не должно
        g_fake = FakeTerminal();
        g_fake.calc_profit_fails = true;
        lot = CalcLotByRisk(1, 1.1000, 1.0980, 1.0, reason);
        check(lot == 0, "OrderCalcProfit не сработал -> вход отменён", reason);
        g_fake.calc_profit_fails = false;
    }

    std::cout << "\n=== 12. Округление объёма до шага брокера ===\n";
    {
        // Регрессия: 0.25/0.01 в двоичной арифметике = 24.999999999999996,
        // из-за чего объём занижался до 0.24. Эпсилон в FloorToStep это лечит.
        check(std::fabs(FloorToStep(0.25, 0.01) - 0.25) < 1e-9,
              "0.25 при шаге 0.01 остаётся 0.25 (регрессия занижения лота)",
              std::to_string(FloorToStep(0.25, 0.01)));
        check(std::fabs(FloorToStep(0.29, 0.01) - 0.29) < 1e-9, "0.29 при шаге 0.01");
        check(std::fabs(FloorToStep(0.257, 0.01) - 0.25) < 1e-9, "0.257 округляется ВНИЗ до 0.25");
        check(std::fabs(FloorToStep(0.2999, 0.1) - 0.2) < 1e-9, "0.2999 при шаге 0.1 -> 0.2");
        check(std::fabs(FloorToStep(1.7, 1.0) - 1.0) < 1e-9, "1.7 при шаге 1.0 -> 1.0");
        check(std::fabs(FloorToStep(0.1234, 0.001) - 0.123) < 1e-9,
              "шаг 0.001 поддерживается", std::to_string(FloorToStep(0.1234, 0.001)));
        check(VolumeDigits(0.01) == 2, "шаг 0.01 -> 2 знака");
        check(VolumeDigits(0.001) == 3, "шаг 0.001 -> 3 знака");
        check(VolumeDigits(1.0) == 0, "шаг 1.0 -> 0 знаков");

        // Брокер с шагом 0.001: объём не должен округляться до 2 знаков вверх
        g_profile = PROFILE_EURUSD;
        _Symbol = "EURUSD";
        g_fake = FakeTerminal();
        g_fake.volume_step = 0.001;
        g_fake.volume_min = 0.001;
        g_fake.contract_size = 100000.0;
        InpMaxLot = 1.0;
        std::string reason;
        // риск 50$, SL 30 пт -> убыток лота 300$ -> 0.1666 -> вниз до 0.166
        double lot = CalcLotByRisk(1, 1.1000, 1.0970, 1.0, reason);
        check(std::fabs(lot - 0.166) < 1e-9, "шаг 0.001: 0.1666 -> 0.166 (вниз, без округления вверх)",
              "получено " + std::to_string(lot));
    }

    std::cout << "\n=== 13. ИИ-ограничитель: может только запрещать и уменьшать ===\n";
    {
        bool allowNew, allowBuy, allowSell;
        double mult;
        std::string why;
        g_fake = FakeTerminal();
        g_fake.now = utc(2026, 1, 15, 12, 0);

        // ИИ выключен -> полная свобода локальной логике
        g_aiEnabled = false;
        GetAIGate(allowNew, allowBuy, allowSell, mult, why);
        check(allowNew && allowBuy && allowSell && mult == 1.0, "ИИ выключен -> ограничений нет");

        // Мост НИКОГДА не отвечал, режим pause -> входы запрещены
        g_aiEnabled = true;
        g_aiLastOkTime = 0;
        InpBridgeFailureMode = BRIDGE_PAUSE;
        GetAIGate(allowNew, allowBuy, allowSell, mult, why);
        check(!allowNew, "мост не отвечал + pause -> входы запрещены", why);

        // Тот же случай, но baseline -> торгуем по локальной логике
        InpBridgeFailureMode = BRIDGE_BASELINE;
        GetAIGate(allowNew, allowBuy, allowSell, mult, why);
        check(allowNew && mult == 1.0, "мост не отвечал + baseline -> локальная логика", why);

        // Кэш устарел (46 минут при лимите 45) -> режим chaos
        g_aiLastOkTime = g_fake.now - 46 * 60;
        g_aiRegime = REGIME_TREND_UP;
        g_aiTradeAllowed = true;
        g_aiRiskMultiplier = 1.0;
        GetAIGate(allowNew, allowBuy, allowSell, mult, why);
        check(!allowNew, "кэш старше 45 минут -> входы запрещены (chaos)", why);

        // Кэш свежий (44 минуты) -> ответ ещё действует
        g_aiLastOkTime = g_fake.now - 44 * 60;
        GetAIGate(allowNew, allowBuy, allowSell, mult, why);
        check(allowNew, "кэш моложе 45 минут -> ответ действует", why);

        // trend_up запрещает продажи, но не покупки
        g_aiRegime = REGIME_TREND_UP;
        GetAIGate(allowNew, allowBuy, allowSell, mult, why);
        check(allowNew && allowBuy && !allowSell, "trend_up -> продажи запрещены", why);

        // trend_down запрещает покупки
        g_aiRegime = REGIME_TREND_DOWN;
        GetAIGate(allowNew, allowBuy, allowSell, mult, why);
        check(allowNew && !allowBuy && allowSell, "trend_down -> покупки запрещены", why);

        // chaos запрещает всё
        g_aiRegime = REGIME_CHAOS;
        GetAIGate(allowNew, allowBuy, allowSell, mult, why);
        check(!allowNew, "chaos -> новые сделки запрещены", why);

        // trade_allowed=false запрещает всё даже при спокойном режиме
        g_aiRegime = REGIME_RANGE;
        g_aiTradeAllowed = false;
        GetAIGate(allowNew, allowBuy, allowSell, mult, why);
        check(!allowNew, "trade_allowed=false -> новые сделки запрещены", why);

        // range: направления свободны, действует множитель
        g_aiTradeAllowed = true;
        g_aiRiskMultiplier = 0.4;
        GetAIGate(allowNew, allowBuy, allowSell, mult, why);
        check(allowNew && allowBuy && allowSell && std::fabs(mult - 0.4) < 1e-9,
              "range -> направления свободны, множитель 0.4", why);

        // КЛЮЧЕВОЕ: множитель > 1 не может увеличить риск
        g_aiRiskMultiplier = 5.0;
        GetAIGate(allowNew, allowBuy, allowSell, mult, why);
        check(mult == 1.0, "множитель 5.0 обрезается до 1.0 — ИИ не может увеличить риск");

        // Отрицательный множитель обрезается до нуля
        g_aiRiskMultiplier = -3.0;
        GetAIGate(allowNew, allowBuy, allowSell, mult, why);
        check(mult == 0.0, "отрицательный множитель обрезается до 0.0");
    }

    std::cout << "\n===========================================\n";
    std::cout << "Пройдено: " << g_passed << ", провалено: " << g_failed << "\n";
    std::cout << "===========================================\n";
    return g_failed == 0 ? 0 : 1;
}
