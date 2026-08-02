// Тесты доработанного расчёта риска в AI_Scalper_Pro.
// Функции берутся напрямую из ai_scalper_pro/RiskManager.mqh
// (см. run_all.sh), поэтому проверяется настоящий код советника.

#include "mql5_shim.h"

// --- Входные параметры и состояние, от которых зависит расчёт ---
bool   UseLossStreakRiskScaling  = true;
int    MaxConsecutiveLosses      = 5;
double MinLossStreakRiskMultiplier = 0.3;
int    g_consecutiveLosses       = 0;

bool   g_effUseRiskPercent       = true;
double g_effRiskPercent          = 1.0;
double g_effLotSize              = 0.01;
bool   AllowMinLotOverRisk       = false;
string g_lastRejectReason        = "";

string _Symbol = "EURUSD";
FakeTerminal g_fake;
std::map<std::string, double> g_globals;
bool g_shim_verbose = false;

// Состояние дня, которое теперь должно переживать перезапуск советника
double DayStartEquity = 0;
double g_peakEquity = 0;
int    TradesToday = 0;
datetime LastTradeDay = 0;
long   MagicNumber = 777;
bool   UseDailyLossLimit = true;
bool   UseMaxDrawdownLimit = true;
double g_effDailyLossLimitPercent = 3.0;
double g_effMaxDrawdownPercent = 10.0;
datetime g_pauseUntil = 0;
int PauseHoursAfterLossStreak = 4;

// Заглушки, не используемые извлечёнными функциями
string InpGoldSessionStartLondon = "";
string InpGoldSessionEndNewYork = "";
int    InpMinutesBeforeWeekClose = 0;
double InpRiskPercentEURUSD = 0, InpRiskPercentGold = 0, InpMaxLot = 0;
int    InpAICacheTTLMinutes = 0;
ENUM_BRIDGE_FAILURE InpBridgeFailureMode = BRIDGE_PAUSE;
ENUM_PROFILE_MODE g_profile = PROFILE_EURUSD;
bool g_aiEnabled = false;
datetime g_aiLastOkTime = 0;
ENUM_AI_REGIME g_aiRegime = REGIME_NONE;
bool g_aiTradeAllowed = true;
double g_aiRiskMultiplier = 1.0;
string g_aiReason = "";

// MoneyRiskPerLot зависит от котировок и OrderCalcProfit — эмулируем так же,
// как это делает терминал для инструментов, котируемых в USD.
double MoneyRiskPerLot(double slDist) {
    if (slDist <= 0) return 0;
    return slDist * g_fake.contract_size;
}

#include "generated_scalper.h"

static int g_failed = 0, g_passed = 0;

static void check(bool ok, const std::string &name, const std::string &detail = "") {
    if (ok) { ++g_passed; std::cout << "  OK   " << name << "\n"; }
    else { ++g_failed; std::cout << "  СБОЙ " << name << (detail.empty() ? "" : "  -> " + detail) << "\n"; }
}

// Момент времени UTC для тестов
static datetime utc(int y, int mo, int d, int h, int mi) {
    MqlDateTime st = {};
    st.year = y; st.mon = mo; st.day = d; st.hour = h; st.min = mi; st.sec = 0;
    return StructToTime(st);
}

static void reset() {
    g_fake = FakeTerminal();
    g_fake.contract_size = 100000.0;   // EURUSD
    g_consecutiveLosses = 0;
    g_effUseRiskPercent = true;
    g_effRiskPercent = 1.0;
    g_effLotSize = 0.01;
    AllowMinLotOverRisk = false;
    g_lastRejectReason = "";
}

int main() {
    std::cout << "\n=== 1. Округление объёма до шага (исправление занижения лота) ===\n";
    // Регрессия: старая формула MathFloor(v/step)*step занижала лот на шаг
    // примерно в 7% случаев из-за двоичной арифметики.
    check(std::fabs(FloorVolumeToStep(0.29, 0.01) - 0.29) < 1e-9,
          "0.29 при шаге 0.01 остаётся 0.29", std::to_string(FloorVolumeToStep(0.29, 0.01)));
    check(std::fabs(FloorVolumeToStep(0.25, 0.01) - 0.25) < 1e-9, "0.25 при шаге 0.01");
    check(std::fabs(FloorVolumeToStep(0.257, 0.01) - 0.25) < 1e-9, "0.257 округляется ВНИЗ");
    check(std::fabs(FloorVolumeToStep(0.1234, 0.001) - 0.123) < 1e-9, "шаг 0.001 поддерживается");
    check(VolumeDigitsOf(0.01) == 2 && VolumeDigitsOf(0.001) == 3 && VolumeDigitsOf(1.0) == 0,
          "число знаков определяется по шагу");

    // Массовая проверка: ни одно «круглое» значение не должно терять шаг
    {
        int lost = 0;
        for (int i = 1; i <= 20000; i++) {
            double v = i / 100.0;
            if (std::fabs(FloorVolumeToStep(v, 0.01) - v) > 1e-9) lost++;
        }
        check(lost == 0, "20 000 значений лота — ни одного занижения",
              "потеряно: " + std::to_string(lost));
    }

    std::cout << "\n=== 2. Снижение риска после серии убытков (только вниз) ===\n";
    reset();
    check(std::fabs(GetLossStreakRiskMultiplier() - 1.0) < 1e-9, "0 убытков -> множитель 1.0");
    g_consecutiveLosses = 5;
    check(GetLossStreakRiskMultiplier() <= 0.31, "у порога паузы -> около 0.3");
    g_consecutiveLosses = 100;
    check(GetLossStreakRiskMultiplier() >= 0.3 - 1e-9,
          "множитель не опускается ниже нижней границы");
    check(GetLossStreakRiskMultiplier() <= 1.0, "множитель никогда не БОЛЬШЕ 1.0 (риск не растёт)");
    g_consecutiveLosses = 0;

    std::cout << "\n=== 3. Расчёт лота по риску ===\n";
    reset();
    // Счёт 10 000, риск 1% = 100$. Стоп 0.0020 -> убыток лота 200$ -> лот 0.5
    g_fake.equity = 10000.0;
    g_effRiskPercent = 1.0;
    std::string r;
    double lot = CalcLot(0.0020);
    check(std::fabs(lot - 0.5) < 1e-9, "риск 1% от 10000, стоп 20 пт -> 0.5 лота",
          "получено " + std::to_string(lot));

    // Вдвое дальше стоп -> вдвое меньше объём
    lot = CalcLot(0.0040);
    check(std::fabs(lot - 0.25) < 1e-9, "стоп вдвое дальше -> лот вдвое меньше",
          "получено " + std::to_string(lot));

    // Серия убытков уменьшает объём
    g_consecutiveLosses = 5;
    double lotAfterLosses = CalcLot(0.0020);
    check(lotAfterLosses < 0.5, "после серии убытков лот МЕНЬШЕ",
          "получено " + std::to_string(lotAfterLosses));
    g_consecutiveLosses = 0;

    std::cout << "\n=== 4. ГЛАВНОЕ: мин. лот больше не ломает лимит риска ===\n";
    reset();
    // Счёт 100$, риск 0.3% = 0.30$. Стоп 100 пт -> убыток мин. лота (0.01) = 1.00$,
    // то есть 1% вместо 0.3%. Раньше сделка открывалась. Теперь — отказ.
    g_fake.equity = 100.0;
    g_effRiskPercent = 0.3;
    lot = CalcLot(0.0100);
    check(lot == 0, "мин. лот рискует больше бюджета -> сделка отменена (было: открывалась)",
          "получено " + std::to_string(lot));
    check(g_lastRejectReason.find("Мин. лот") != std::string::npos,
          "причина отказа записана в журнал", g_lastRejectReason);

    // Тот же случай, но пользователь осознанно разрешил превышение
    AllowMinLotOverRisk = true;
    lot = CalcLot(0.0100);
    check(std::fabs(lot - 0.01) < 1e-9,
          "с AllowMinLotOverRisk=true сделка открывается мин. лотом",
          "получено " + std::to_string(lot));
    AllowMinLotOverRisk = false;

    // Счёт больше — мин. лот укладывается в бюджет, сделка разрешена.
    // Стоп 100 пт: 1 лот теряет 1000$, значит мин. лот 0.01 теряет 10$.
    // Бюджет должен быть не меньше 10$ -> при риске 0.3% нужен счёт от ~3333$.
    reset();
    g_fake.equity = 5000.0;
    g_effRiskPercent = 0.3;   // бюджет 15$ против убытка мин. лота 10$
    lot = CalcLot(0.0100);
    check(lot >= 0.01, "на большем счёте сделка проходит", "получено " + std::to_string(lot));

    // И граница: чуть меньший счёт -> бюджета уже не хватает, отказ
    reset();
    g_fake.equity = 3000.0;   // бюджет 9$ против убытка мин. лота 10$
    g_effRiskPercent = 0.3;
    lot = CalcLot(0.0100);
    check(lot == 0, "на счёте чуть меньше порога — отказ (граница работает)",
          "получено " + std::to_string(lot));

    std::cout << "\n=== 5. Золото: другой размер контракта ===\n";
    reset();
    _Symbol = "XAUUSD";
    g_fake.contract_size = 100.0;      // 100 унций в лоте
    g_fake.equity = 10000.0;
    g_effRiskPercent = 0.5;            // бюджет 50$
    lot = CalcLot(5.0);                // стоп 5$ -> убыток лота 500$ -> 0.1
    check(std::fabs(lot - 0.1) < 1e-9, "риск 0.5% от 10000, стоп 5$ -> 0.1 лота",
          "получено " + std::to_string(lot));
    _Symbol = "EURUSD";

    std::cout << "\n=== 6. Защита от некорректных данных ===\n";
    reset();
    lot = CalcLot(0);
    check(lot > 0, "нулевой стоп -> запасной лот, а не ноль/мусор",
          "получено " + std::to_string(lot));
    reset();
    g_fake.equity = 0;
    lot = CalcLot(0.0020);
    check(lot > 0, "нулевой депозит -> запасной лот", "получено " + std::to_string(lot));

    reset();
    g_fake.volume_max = 0.05;          // брокер ограничил максимум
    g_fake.equity = 1000000.0;
    lot = CalcLot(0.0020);
    check(lot <= 0.05 + 1e-9, "потолок объёма брокера соблюдается",
          "получено " + std::to_string(lot));

    std::cout << "\n=== 7. Дневное состояние переживает перезапуск советника ===\n";
    {
        // Имитация: OnInit() вызывается заново при смене таймфрейма или
        // правке любого параметра. Раньше это молча обнуляло дневной лимит.
        auto restart_ea = [&]() { LoadDailyState(); };

        g_globals.clear();
        g_fake = FakeTerminal();
        g_fake.now = utc(2026, 3, 10, 9, 0);
        g_fake.equity = 10000.0;
        TradesToday = 0;

        restart_ea();  // первый запуск
        check(std::fabs(DayStartEquity - 10000.0) < 1e-9,
              "первый запуск: equity начала дня записана",
              std::to_string(DayStartEquity));

        // Торговали, потеряли 2.5%, сделали 7 сделок
        TradesToday = 7;
        SaveDailyState();
        g_fake.equity = 9750.0;

        // Пользователь переключил таймфрейм -> OnInit сработал заново
        DayStartEquity = 0; g_peakEquity = 0; TradesToday = 0;  // память очищена
        restart_ea();

        check(std::fabs(DayStartEquity - 10000.0) < 1e-9,
              "после перезапуска equity начала дня СОХРАНИЛАСЬ (было: обнулялась)",
              std::to_string(DayStartEquity));
        check(TradesToday == 7, "счётчик сделок за день сохранился",
              std::to_string(TradesToday));

        // Дневной лимит должен видеть реальную потерю 2.5%, а не ноль
        g_effDailyLossLimitPercent = 2.0;
        check(DailyLossLimitHit(),
              "дневной лимит 2% срабатывает на реальной потере 2.5%");

        // А если бы состояние обнулилось — лимит бы не сработал
        DayStartEquity = g_fake.equity;
        check(!DailyLossLimitHit(),
              "проверка теста: при обнулённом состоянии лимит НЕ срабатывает (старое поведение)");
        restart_ea();

        std::cout << "\n=== 8. Новый день начинается заново ===\n";
        g_fake.now = utc(2026, 3, 11, 9, 0);   // следующий день
        g_fake.equity = 9750.0;
        restart_ea();
        check(std::fabs(DayStartEquity - 9750.0) < 1e-9,
              "новый день: equity начала дня взята заново",
              std::to_string(DayStartEquity));
        check(TradesToday == 0, "новый день: счётчик сделок обнулён",
              std::to_string(TradesToday));
        g_effDailyLossLimitPercent = 2.0;
        check(!DailyLossLimitHit(), "новый день: лимит убытка снят");

        std::cout << "\n=== 9. Разделение состояния между счетами и парами ===\n";
        // Счётчик сделок — свой у каждой пары "инструмент + magic"
        TradesToday = 5; SaveDailyState();
        std::string nameEur = StateGVTradesName();
        _Symbol = "XAUUSD";
        std::string nameGold = StateGVTradesName();
        check(nameEur != nameGold, "у разных инструментов разные счётчики");
        TradesToday = 0;
        restart_ea();
        check(TradesToday == 0, "золото не видит счётчик EURUSD",
              std::to_string(TradesToday));
        _Symbol = "EURUSD";
        restart_ea();
        check(TradesToday == 5, "EURUSD помнит свой счётчик",
              std::to_string(TradesToday));

        // Другой счёт — полностью отдельное состояние
        std::string prefixA = StateGVPrefix();
        g_fake.login = 999999;
        std::string prefixB = StateGVPrefix();
        check(prefixA != prefixB, "у разных счетов разные имена переменных");
        g_fake.login = 123456;

        std::cout << "\n=== 10. Пик equity для контроля просадки ===\n";
        g_globals.clear();
        g_fake.now = utc(2026, 3, 12, 9, 0);
        g_fake.equity = 10000.0;
        restart_ea();
        g_fake.equity = 12000.0;
        g_effMaxDrawdownPercent = 10.0;
        MaxDrawdownHit();                       // фиксирует новый пик 12000
        check(std::fabs(g_peakEquity - 12000.0) < 1e-9, "новый пик записан",
              std::to_string(g_peakEquity));

        g_peakEquity = 0;                       // память очищена перезапуском
        restart_ea();
        check(std::fabs(g_peakEquity - 12000.0) < 1e-9,
              "пик сохранился после перезапуска", std::to_string(g_peakEquity));

        g_fake.equity = 10500.0;                // просадка 12.5% от пика
        check(MaxDrawdownHit(),
              "просадка считается от НАСТОЯЩЕГО пика, а не от заниженного");
    }

    std::cout << "\n=== 11. Пауза после серии убытков переживает перезапуск ===\n";
    {
        g_globals.clear();
        g_fake = FakeTerminal();
        g_fake.now = utc(2026, 3, 13, 20, 0);
        g_fake.equity = 10000.0;
        LoadDailyState();

        // Сработала серия убытков: пауза на 4 часа
        g_consecutiveLosses = 0;
        g_pauseUntil = g_fake.now + 4 * 3600;
        SaveRiskStreakState();
        check(LossStreakPauseActive(), "пауза активна сразу после срабатывания");

        // Пользователь переключил таймфрейм — советник перезапустился
        g_pauseUntil = 0;
        g_consecutiveLosses = 0;
        LoadDailyState();
        check(LossStreakPauseActive(),
              "пауза ПЕРЕЖИЛА перезапуск (было: снималась переключением таймфрейма)");

        // Серия убытков тоже восстанавливается
        g_consecutiveLosses = 3;
        SaveRiskStreakState();
        g_consecutiveLosses = 0;
        LoadDailyState();
        check(g_consecutiveLosses == 3, "серия убытков восстановлена",
              std::to_string(g_consecutiveLosses));
        check(GetLossStreakRiskMultiplier() < 1.0,
              "значит и сниженный риск после убытков сохраняется");

        // Пауза заканчивается по времени, а не по перезапуску
        g_fake.now += 5 * 3600;
        check(!LossStreakPauseActive(), "через 5 часов пауза закончилась сама");

        // Смена дня не должна снимать паузу, если она ещё идёт
        g_fake.now = utc(2026, 3, 13, 23, 30);
        g_pauseUntil = g_fake.now + 2 * 3600;   // пауза уходит за полночь
        SaveRiskStreakState();
        g_fake.now = utc(2026, 3, 14, 0, 30);   // наступил следующий день
        g_pauseUntil = 0;
        LoadDailyState();                        // начнётся новый день
        check(LossStreakPauseActive(),
              "пауза, начатая вечером, действует и после полуночи");
        check(TradesToday == 0, "при этом новый день начат: счётчик сделок обнулён");
    }

    std::cout << "\n===========================================\n";
    std::cout << "Пройдено: " << g_passed << ", провалено: " << g_failed << "\n";
    std::cout << "===========================================\n";
    return g_failed == 0 ? 0 : 1;
}
