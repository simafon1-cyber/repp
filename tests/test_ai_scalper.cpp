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

    std::cout << "\n===========================================\n";
    std::cout << "Пройдено: " << g_passed << ", провалено: " << g_failed << "\n";
    std::cout << "===========================================\n";
    return g_failed == 0 ? 0 : 1;
}
