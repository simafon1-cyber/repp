//+------------------------------------------------------------------+
//| RiskManager.mqh                                                  |
//| Всё, что решает СКОЛЬКО и МОЖНО ЛИ торговать: профили риска,     |
//| размер лота, TP/SL дистанции, дневные лимиты и защитные фильтры  |
//| (спред/время). Это "тормоза и руль" советника.                   |
//+------------------------------------------------------------------+

//===================== ПРОСТОЙ РЕЖИМ: ПРИМЕНЕНИЕ ПРОФИЛЯ (п.15) =========
void ApplyRiskProfile()
{
   if(!UseSimpleProfile)
   {
      // Ручной режим — всё как задано в Advanced-инпутах, без изменений
      g_effUseRiskPercent        = UseRiskPercent;
      g_effRiskPercent           = RiskPercent;
      g_effLotSize               = LotSize;
      g_effATRSLMultiplier       = ATRSLMultiplier;
      g_effUseMoneyTP            = UseMoneyTP;
      g_effTargetProfitMoney     = TargetProfitMoney;
      g_effMinScoreToTrade       = MinScoreToTrade;
      g_effMaxOpenPositions      = MaxOpenPositions;
      g_effMaxTradesPerDay       = MaxTradesPerDay;
      g_effDailyLossLimitPercent = DailyLossLimitPercent;
      g_effMaxDrawdownPercent    = MaxDrawdownPercent;
      g_effMaxTotalRiskPercent   = MaxTotalRiskPercent;
      g_effIgnoreSoftFilters     = false;
      g_effHedgeBothDirections   = false; // хедж пока доступен только через профиль "Истеричка"
      g_activeProfileName        = "Ручной (Advanced)";
      return;
   }

   g_effUseRiskPercent   = true;    // в простом режиме лот всегда считается по риску — так безопаснее
   g_effLotSize          = LotSize; // fallback, если риск посчитать не получится (напр. нет тик-данных)
   g_effIgnoreSoftFilters = false;  // по умолчанию выключено — включается только у "Истерички"
   g_effHedgeBothDirections = false; // по умолчанию выключено — включается только у "Истерички"

   switch(RiskProfile)
   {
      case PROFILE_CONSERVATIVE:
         g_effRiskPercent           = 0.3;
         g_effATRSLMultiplier       = 2.5;
         g_effUseMoneyTP            = true;
         g_effTargetProfitMoney     = 2.0;
         g_effMinScoreToTrade       = 70;
         g_effMaxOpenPositions      = 1;
         g_effMaxTradesPerDay       = 0;   // 0 = без ограничения
         g_effDailyLossLimitPercent = 2.0;
         g_effMaxDrawdownPercent    = 6.0;
         g_effMaxTotalRiskPercent   = 0.5;  // ~1 сделка × 0.3% с небольшим запасом
         g_activeProfileName        = "Консервативный";
         break;

      case PROFILE_AGGRESSIVE:
         // п.21: TargetProfitMoney поднят с 3.0 до 8.0 по факту наблюдения на демо —
         // при риске 1.2% (~12$ на счёте ~1000$) и цели всего 3$ безубыточный винрейт
         // получался ~80% (12/(12+3)), что нереально даже при хорошем сигнале: винрейт
         // вырос до 56% после анти-дребезга (п.20), но итог всё равно в минусе именно
         // из-за этого перекоса риск:прибыль, а не из-за качества сигналов. TargetProfitMoney=8
         // даёт безубыточный порог ~60% (12/(12+8)) — сопоставимо со Сбалансированным (~64%).
         g_effRiskPercent           = 1.2;
         g_effATRSLMultiplier       = 2.0;
         g_effUseMoneyTP            = true;
         g_effTargetProfitMoney     = 8.0;
         g_effMinScoreToTrade       = 55;
         g_effMaxOpenPositions      = 5;
         g_effMaxTradesPerDay       = 0;   // 0 = без ограничения
         g_effDailyLossLimitPercent = 5.0;
         g_effMaxDrawdownPercent    = 15.0;
         g_effMaxTotalRiskPercent   = 6.5;  // ~5 сделок × 1.2% с небольшим запасом —
                                             // иначе фиксированный потолок раньше "душил" стек сделок
         g_activeProfileName        = "Агрессивный";
         break;

      case PROFILE_HYSTERIC:
         // "Истеричка": почти никаких фильтров-условий на вход, но лоты и риск
         // на сделку — минимальные. Дневной лимит убытка/просадка НЕ отключаются —
         // это аварийные стоп-краны, а не фильтр качества сигнала (п.17).
         // Порог score поднят с 30 до 45 по факту РЕАЛЬНЫХ сделок пользователя
         // (скриншот истории: BUY и SELL по одному символу открывались почти
         // одновременно со слабыми сигналами) — порог 30 из 100 пропускал почти
         // любой шум. 45 всё ещё намного мягче Агрессивного (55) и
         // Сбалансированного (62), но отсекает сигналы нулевого качества.
         g_effRiskPercent           = 0.1;
         g_effATRSLMultiplier       = 1.5;
         g_effUseMoneyTP            = true;
         g_effTargetProfitMoney     = 1.0;
         g_effMinScoreToTrade       = 45;
         g_effMaxOpenPositions      = 10;
         g_effMaxTradesPerDay       = 0;   // 0 = без ограничения
         g_effDailyLossLimitPercent = 8.0;
         g_effMaxDrawdownPercent    = 25.0;
         g_effMaxTotalRiskPercent   = 3.0;   // 10 сделок × 0.1% ≈ 1% — запас на случай проскальзывания
         g_effIgnoreSoftFilters     = true;  // время/новости/волатильность/издержки-vs-TP — игнор
         // ОТКЛЮЧЕНО по факту реальных сделок пользователя (скриншот: обе ноги
         // хеджа теряли на спреде во флэте — напр. nzdjpys buy И sell ОБЕ в
         // минусе одновременно) — хедж гарантированно платит двойной спред, а
         // выигрывает только когда рынок УЖЕ явно направлен. Раньше было true
         // по просьбе пользователя, теперь выключено по его же решению после
         // реальных убытков. Переключатель остался в Advanced/ручном режиме.
         g_effHedgeBothDirections   = false;
         g_activeProfileName        = "Истеричка (YOLO)";
         break;

      default: // PROFILE_BALANCED
         g_effRiskPercent           = 0.7;
         g_effATRSLMultiplier       = 2.5;
         g_effUseMoneyTP            = true;
         g_effTargetProfitMoney     = 4.0;
         g_effMinScoreToTrade       = 62;
         g_effMaxOpenPositions      = 2;
         g_effMaxTradesPerDay       = 0;   // 0 = без ограничения
         g_effDailyLossLimitPercent = 3.0;
         g_effMaxDrawdownPercent    = 10.0;
         g_effMaxTotalRiskPercent   = 1.8;  // ~2 сделки × 0.7% с небольшим запасом
         g_activeProfileName        = "Сбалансированный";
         break;
   }
}

//===================== ДНЕВНОЕ СОСТОЯНИЕ: ПЕРЕЖИВАЕТ ПЕРЕЗАПУСК (п.25) ==
// Раньше DayStartEquity, TradesToday и пиковая equity жили только в памяти и
// заново инициализировались в OnInit(). А OnInit() срабатывает не только при
// перезапуске терминала, но и при СМЕНЕ ТАЙМФРЕЙМА графика и при изменении
// ЛЮБОГО входного параметра — то есть при самых обычных действиях.
// В результате дневной лимит убытка и счётчик сделок за день молча обнулялись:
// советник, потерявший 2.5% за день, после переключения M5->M15 начинал день
// заново и мог потерять лимит ещё раз.
// Теперь состояние хранится в глобальных переменных терминала: они переживают
// перезапуск советника и самого MetaTrader.
//
// Equity — величина уровня СЧЁТА, поэтому баланс начала дня и пик общие для
// всех графиков. Счётчик сделок — свой у каждой пары "инструмент + magic".

// Номер дня по времени сервера (совпадает с логикой CheckNewDay)
long StateDaySerial()
{
   return (long)(TimeTradeServer()/86400);
}

string StateGVPrefix()
{
   return "AISP."+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+".";
}

// Префикс состояния конкретного экземпляра советника: свой для каждой пары
// "инструмент + MagicNumber". Счётчик сделок, серия убытков и пауза — свои
// у каждого графика, а equity начала дня общая (это уровень счёта).
string StateGVInstance()
{
   return StateGVPrefix()+_Symbol+"."+IntegerToString(MagicNumber)+".";
}

string StateGVTradesName()
{
   return StateGVInstance()+"trades";
}

double StateGVGet(const string name,double def)
{
   if(!GlobalVariableCheck(name)) return def;
   return GlobalVariableGet(name);
}

// Сохраняет текущее дневное состояние (вызывается после каждого изменения)
void SaveDailyState()
{
   string p=StateGVPrefix();
   GlobalVariableSet(p+"day",     (double)StateDaySerial());
   GlobalVariableSet(p+"dayeq",   DayStartEquity);
   GlobalVariableSet(p+"peakeq",  g_peakEquity);
   GlobalVariableSet(StateGVTradesName(), (double)TradesToday);
}

// Серия убытков и пауза после неё. Хранятся ОТДЕЛЬНО от дневного состояния:
// пауза может начаться в 23:00 и закончиться уже на следующий день, поэтому
// сменой дня она не сбрасывается. Раньше и то и другое жило только в памяти —
// значит аварийную паузу можно было случайно снять, просто переключив
// таймфрейм графика (это перезапускает советника).
void SaveRiskStreakState()
{
   string p=StateGVInstance();
   GlobalVariableSet(p+"losses", (double)g_consecutiveLosses);
   GlobalVariableSet(p+"pause",  (double)g_pauseUntil);
}

void LoadRiskStreakState()
{
   string p=StateGVInstance();
   g_consecutiveLosses=(int)StateGVGet(p+"losses",0);
   g_pauseUntil=(datetime)(long)StateGVGet(p+"pause",0);
   if(g_consecutiveLosses<0) g_consecutiveLosses=0;
   if(g_pauseUntil>TimeCurrent())
      PrintFormat("Восстановлена пауза после серии убытков, до %s",
                  TimeToString(g_pauseUntil,TIME_DATE|TIME_MINUTES));
}

// Начинает новый торговый день: запоминает стартовую equity и обнуляет счётчик
void StartNewDayState()
{
   DayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_peakEquity   = DayStartEquity;
   TradesToday    = 0;
   LastTradeDay   = TimeCurrent();
   SaveDailyState();
   PrintFormat("Новый торговый день. Equity начала дня: %.2f", DayStartEquity);
}

// Восстанавливает состояние при запуске советника.
// Если сохранённое состояние от СЕГОДНЯШНЕГО дня — продолжаем его,
// иначе начинаем новый день.
void LoadDailyState()
{
   LoadRiskStreakState(); // серия убытков и пауза не зависят от смены дня

   string p=StateGVPrefix();
   long savedDay=(long)StateGVGet(p+"day",-1);

   if(savedDay==StateDaySerial())
   {
      DayStartEquity = StateGVGet(p+"dayeq", 0);
      g_peakEquity   = StateGVGet(p+"peakeq", 0);
      TradesToday    = (int)StateGVGet(StateGVTradesName(), 0);
      LastTradeDay   = TimeCurrent();

      // Подстраховка на случай испорченных значений
      double equity=AccountInfoDouble(ACCOUNT_EQUITY);
      if(DayStartEquity<=0) DayStartEquity=equity;
      if(g_peakEquity<DayStartEquity) g_peakEquity=DayStartEquity;
      if(TradesToday<0) TradesToday=0;

      PrintFormat("Дневное состояние восстановлено: equity начала дня %.2f, сделок сегодня %d",
                  DayStartEquity, TradesToday);
      return;
   }

   StartNewDayState();
}

//===================== ПОЗИЦИИ ПО MAGIC ================
int CountOpenPositions()
{
   int count=0;
   for(int i=0;i<PositionsTotal();i++)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket<=0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=(long)MagicNumber) continue;
      count++;
   }
   return count;
}

//===================== ТОЧНЫЙ РАСЧЁТ ДЕНЕГ (п.25) ======================
// Раньше все денежные расчёты шли через SYMBOL_TRADE_TICK_VALUE. Для EURUSD
// это работает, но на золоте и кроссах, где валюта прибыли не совпадает с
// валютой счёта, у части брокеров tick value неточен или обновляется с
// задержкой — риск считался неверно именно на тех инструментах, где цена
// ошибки выше всего. OrderCalcProfit спрашивает точную сумму у самого
// терминала, с учётом контракта и конвертации валют конкретного брокера.
// Возвращает 0, если посчитать не удалось (вызывающий код обязан проверить).
double MoneyPerDistance(int direction,double dist,double lot)
{
   if(dist<=0 || lot<=0) return 0;
   double price=SymbolInfoDouble(_Symbol,(direction>0)?SYMBOL_ASK:SYMBOL_BID);
   if(price<=0) return 0;
   double closePrice=(direction>0)?(price+dist):(price-dist);
   if(closePrice<=0) return 0;
   double profit=0;
   ENUM_ORDER_TYPE ot=(direction>0)?ORDER_TYPE_BUY:ORDER_TYPE_SELL;
   if(!OrderCalcProfit(ot,_Symbol,lot,price,closePrice,profit)) return 0;
   return MathAbs(profit);
}

// Сколько денег теряет 1.0 лот на дистанции стопа
double MoneyRiskPerLot(double slDist)
{
   double m=MoneyPerDistance(1,slDist,1.0);
   if(m>0) return m;
   // Запасной путь через tick value — на случай, если OrderCalcProfit недоступен
   double tickValue=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   double tickSize =SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(tickValue<=0 || tickSize<=0) return 0;
   return (slDist/tickSize)*tickValue;
}

// Знаков после запятой у шага объёма: 0.01 -> 2, 0.001 -> 3
int VolumeDigitsOf(double step)
{
   for(int d=0; d<=8; d++)
   {
      double scaled=step*MathPow(10,d);
      if(MathAbs(scaled-MathRound(scaled))<1e-9) return d;
   }
   return 2;
}

// Округление объёма ВНИЗ до шага брокера.
// Эпсилон обязателен: в двоичной арифметике 0.29/0.01 = 28.999999999999996,
// и без него лот молча занижался на один шаг. Проверено численно: на
// случайных значениях шаг терялся примерно в 7% случаев.
double FloorVolumeToStep(double volume,double step)
{
   if(step<=0) return volume;
   int digits=VolumeDigitsOf(step);
   double steps=MathFloor(volume/step+1e-9);
   return NormalizeDouble(steps*step,digits);
}

//===================== ЖЁСТКИЕ ЗАЩИТНЫЕ ПРОВЕРКИ =======
// Эти проверки НЕ входят в скоринг — они про исполнение и риск-менеджмент,
// а не про качество сигнала. Пропускать их из-за "высокого score" небезопасно:
// широкий спред или закрытая сессия одинаково вредны при любом сигнале.
bool SpreadOK()
{
   if(!UseSpreadFilter) return true;
   double maxSpreadPts=EffPointsThreshold(MaxSpreadPoints,0.4); // п.24: авто — не больше 40% текущего ATR
   return SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) <= maxSpreadPts;
}
bool TimeFilterOk()
{
   if(g_effIgnoreSoftFilters) return true; // "Истеричка" (п.17) — торгует круглосуточно, без исключений
   if(!UseTimeFilter) return true;
   MqlDateTime tm; TimeToStruct(TimeCurrent(), tm);
   if(StartHour<EndHour) return (tm.hour>=StartHour && tm.hour<EndHour);
   return (tm.hour>=StartHour || tm.hour<EndHour);
}
// п.20: анти-дребезг. НЕ проверяется через g_effIgnoreSoftFilters — это не
// фильтр "качества" сигнала, а защита от буквального самообстрела: без него
// бот видит слабый сигнал в одну сторону, тут же слабый сигнал в другую, и
// сливает спред+SL на обоих, хотя рынок всё это время просто шумел на месте.
bool ReversalCooldownOk(int direction)
{
   if(g_lastCloseDirection==0) return true; // сделок ещё не было
   if(direction!=-g_lastCloseDirection) return true; // это не разворот, а то же направление — ок
   return (g_barCounter-g_lastCloseBarIndex) >= MinBarsBetweenReversal;
}

bool HardSafetyChecks()
{
   if(!SpreadOK())      return false;
   if(!TimeFilterOk())  return false;
   if(!NewsFilterOk())  return false;
   if(GetATRValue()<=0) return false; // индикаторы ещё не готовы
   return true;
}

//===================== ЗАЩИТА ОТ ВСПЛЕСКОВ ВОЛАТИЛЬНОСТИ (п.16) ========
// Статичные правила плохо переживают резкие скачки волатильности (флэш-движения,
// гэпы, аномальные новости) — это одна из главных причин, почему боты "ловят нож".
// Если текущий ATR аномально выше среднего — просто пропускаем сигнал на этом баре.
bool VolatilityOk()
{
   if(g_effIgnoreSoftFilters) return true; // "Истеричка" (п.17) — ей плевать на всплески волатильности
   if(!UseVolatilitySpikeGuard) return true;
   double atrArr[];
   if(CopyBuffer(ATRHandle,0,0,ATRAvgPeriod+1,atrArr)<=0) return true; // не смогли посчитать — не блокируем
   double sum=0; for(int i=1;i<=ATRAvgPeriod;i++) sum+=atrArr[i];
   double avg=sum/ATRAvgPeriod;
   if(avg<=0) return true;
   return (atrArr[0]/avg) <= VolatilitySpikeMultiplier;
}

//===================== РОЛЛОВЕРНАЯ ДЫРА ЛИКВИДНОСТИ (п.22) =============
// В момент смены торгового дня у брокера спред может кратковременно раздуться
// в разы без реального движения цены — по опыту трейдеров это частая причина
// "необъяснимого" срабатывания стопа. Считается soft-фильтром (как VolatilityOk):
// про издержки/качество исполнения, а не про сам сигнал, поэтому "Истеричка" его
// тоже игнорирует — так же, как игнорирует всплески волатильности.
bool RolloverGuardOk()
{
   if(g_effIgnoreSoftFilters) return true; // "Истеричка" (п.17) — не боится и ролловерной дыры
   if(!UseRolloverGuard) return true;
   MqlDateTime tm; TimeToStruct(TimeCurrent(), tm);
   int nowMinutes=tm.hour*60+tm.min;
   int rolloverMinutes=RolloverHourServer*60;
   int diff=MathAbs(nowMinutes-rolloverMinutes);
   diff=MathMin(diff, 1440-diff); // учитываем переход через полночь
   return diff > RolloverGuardMinutes;
}

//===================== ПЛАВНОЕ СНИЖЕНИЕ РИСКА ПО СЕРИИ УБЫТКОВ (п.22) ==
// По опыту трейдеров/MQL5-блогов: жёсткая пауза только ПОСЛЕ N убытков подряд
// слишком грубая — лучше плавно урезать риск по мере приближения к порогу паузы,
// а не торговать полным риском вплоть до самого срабатывания MaxConsecutiveLosses.
double GetLossStreakRiskMultiplier()
{
   if(!UseLossStreakRiskScaling) return 1.0;
   if(MaxConsecutiveLosses<=0) return 1.0;
   double ratio=(double)g_consecutiveLosses/(double)MaxConsecutiveLosses;
   double mult=1.0-0.7*ratio; // 0 убытков -> 1.0, у порога паузы -> ~0.3
   return MathMax(MinLossStreakRiskMultiplier, MathMin(1.0, mult));
}

//===================== ИЗДЕРЖКИ ПРОТИВ TP (п.16) ========================
// По опыту скальперов: если спред "съедает" большую часть цели прибыли — сделка
// в среднем убыточна ещё до движения цены. Проверяем ДО отправки ордера.
bool SpreadCostOk(double lot,double tpDist)
{
   if(g_effIgnoreSoftFilters) return true; // "Истеричка" (п.17) — издержки её не волнуют, лот и так крошечный
   if(tpDist<=0) return true;
   double tickValue=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   double tickSize =SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(tickValue<=0 || tickSize<=0) return true;

   long spreadPts=SymbolInfoInteger(_Symbol,SYMBOL_SPREAD);
   double spreadDist=spreadPts*_Point;
   double spreadMoney=(spreadDist/tickSize)*tickValue*lot;
   double tpMoney=(tpDist/tickSize)*tickValue*lot;
   if(tpMoney<=0) return true;

   return (spreadMoney/tpMoney*100.0) <= MaxSpreadCostPercentOfTP;
}

//===================== СОВОКУПНЫЙ РИСК ПО ВСЕМ ОТКРЫТЫМ СДЕЛКАМ (п.16) ==
// MaxOpenPositions разрешает несколько сделок сразу, но на ОДНОМ инструменте это
// не диверсификация, а умножение риска. Считаем реальный риск (до SL) по каждой
// открытой позиции этого EA и держим сумму под потолком MaxTotalRiskPercent.
double GetOpenRiskPercent()
{
   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity<=0) return 0;

   double totalRiskMoney=0;
   for(int i=0;i<PositionsTotal();i++)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket<=0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=(long)MagicNumber) continue;

      double openPrice=PositionGetDouble(POSITION_PRICE_OPEN);
      double sl=PositionGetDouble(POSITION_SL);
      double volume=PositionGetDouble(POSITION_VOLUME);
      if(sl<=0) continue; // без стопа риск не посчитать (у этого EA такого не бывает, но на всякий случай)

      // п.25: точная сумма от терминала вместо tick value — корректно на золоте и кроссах
      long   ptype=PositionGetInteger(POSITION_TYPE);
      double profit=0;
      ENUM_ORDER_TYPE ot=(ptype==POSITION_TYPE_BUY)?ORDER_TYPE_BUY:ORDER_TYPE_SELL;
      if(OrderCalcProfit(ot,_Symbol,volume,openPrice,sl,profit))
         totalRiskMoney += MathAbs(profit);
      else
      {
         // Запасной путь, если терминал не смог посчитать
         double tickValue=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
         double tickSize =SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
         if(tickValue>0 && tickSize>0)
            totalRiskMoney += (MathAbs(openPrice-sl)/tickSize)*tickValue*volume;
      }
   }
   return totalRiskMoney/equity*100.0;
}

//===================== ПРОСАДКА / СЕРИИ УБЫТКОВ (п.8,9) =
bool DailyLossLimitHit()
{
   if(!UseDailyLossLimit || DayStartEquity<=0) return false;
   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   double diff=(equity-DayStartEquity)/DayStartEquity*100.0;
   return diff <= -MathAbs(g_effDailyLossLimitPercent);
}
bool MaxDrawdownHit()
{
   if(!UseMaxDrawdownLimit) return false;
   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity>g_peakEquity)
   {
      g_peakEquity=equity;
      // п.25: новый пик сохраняем сразу — иначе после перезапуска советника
      // просадка считалась бы от заниженного пика и лимит не сработал бы
      GlobalVariableSet(StateGVPrefix()+"peakeq", g_peakEquity);
   }
   if(g_peakEquity<=0) return false;
   double dd=(g_peakEquity-equity)/g_peakEquity*100.0;
   return dd >= g_effMaxDrawdownPercent;
}
bool LossStreakPauseActive()
{
   return TimeCurrent() < g_pauseUntil;
}
bool TradingAllowed()
{
   if(DailyLossLimitHit())    return false;
   if(MaxDrawdownHit())       return false;
   if(LossStreakPauseActive()) return false;
   return true;
}

//===================== ЛОТ / СТОПЫ ======================
// ВАЖНО: возвращает 0, если сделку открывать нельзя (риск минимального лота
// больше заданного). Вызывающий код обязан проверить результат.
double CalcLot(double slDist)
{
   // п.22: плавное снижение риска по мере серии убытков (действует и на
   // риск-процентный, и на фиксированный лот — иначе фиксированный режим
   // просто игнорировал бы эту защиту)
   double mult=GetLossStreakRiskMultiplier();
   double minLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double lotStep=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);

   if(!g_effUseRiskPercent)
   {
      double lotFixed=FloorVolumeToStep(g_effLotSize*mult,lotStep);
      return MathMax(minLot, MathMin(maxLot, lotFixed));
   }

   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   double riskMoney=equity*g_effRiskPercent/100.0*mult;
   if(riskMoney<=0 || slDist<=0) return FloorVolumeToStep(g_effLotSize,lotStep);

   double lossPerLot=MoneyRiskPerLot(slDist);
   if(lossPerLot<=0) return FloorVolumeToStep(g_effLotSize,lotStep); // не смогли посчитать — запасной лот

   double lot=FloorVolumeToStep(riskMoney/lossPerLot, lotStep);

   // Расчётный лот меньше минимального у брокера: минимальный лот рискует
   // БОЛЬШЕ, чем разрешено. По умолчанию сделку не открываем (см.
   // AllowMinLotOverRisk в Config.mqh) — иначе лимит риска был бы фикцией.
   if(lot<minLot)
   {
      double minLotRisk=lossPerLot*minLot;
      if(minLotRisk>riskMoney && !AllowMinLotOverRisk)
      {
         g_lastRejectReason=StringFormat(
            "Мин. лот %.2f рискует %.2f при бюджете %.2f (%.2f%% от %.2f) — вход отменён",
            minLot, minLotRisk, riskMoney, g_effRiskPercent, equity);
         return 0;
      }
      lot=minLot;
   }

   if(lot>maxLot) lot=FloorVolumeToStep(maxLot,lotStep);
   return lot;
}
bool CheckStopsDistance(double price,double sl,double tp)
{
   long stopLevel=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   double minDist=stopLevel*_Point;
   if(minDist<=0) return true;
   // tp==0 / sl==0 означает "не выставлен вообще" (см. UseMaxProfitRide) — не
   // проверяем дистанцию для несуществующего стопа/цели, иначе ExecuteMarketOrder
   // отменял бы КАЖДЫЙ ордер в режиме максимальной прибыли.
   if(sl!=0 && MathAbs(price-sl)<minDist) return false;
   if(tp!=0 && MathAbs(price-tp)<minDist) return false;
   return true;
}
// КРИТИЧНО (п.58 задачи пользователя): жёсткий пол Risk:Reward — TP никогда не
// опускается ниже slDist*MinRiskRewardRatio, даже если конкретный расчёт (по RR
// из инпута или по денежной цели профиля) дал меньше. Добавлено по факту
// реальных сделок пользователя (скриншот: TP +$1 против SL -$30...-$111+, счёт
// в минусе) — раньше TargetProfitMoney был фиксированной константой, никак не
// привязанной к реальному денежному риску сделки.
double ApplyMinRiskRewardFloor(double tpDist,double slDist)
{
   if(MinRiskRewardRatio<=0 || slDist<=0) return tpDist;
   return MathMax(tpDist, slDist*MinRiskRewardRatio);
}
// TP = SL*RR, зажатый в [TPMinPoints, TPMaxPoints] (Advanced/ручной режим, п.5)
double CalcTPDistance(double slDist)
{
   double tpPoints=(slDist/_Point)*RiskRewardRatio;
   double minPts=EffPointsThreshold(TPMinPoints,0.3); // п.24: авто под ATR инструмента
   double maxPts=EffPointsThreshold(TPMaxPoints,8.0);
   tpPoints=MathMax(minPts, MathMin(maxPts, tpPoints));
   double tpDist=tpPoints*_Point;
   return ApplyMinRiskRewardFloor(tpDist, slDist);
}
// TP по целевой сумме в деньгах (п.14) — при заданном лоте считает, сколько пунктов
// нужно пройти цене, чтобы прибыль составила ровно targetMoney (валюта счёта).
// Снизу НЕ обрезается искусственным TPMinPoints — маленький TP это цель
// пользователя, а не ошибка; сверху остаётся TPMaxPoints как защита от абсурда.
// slDist (по умолчанию -1 = не передан) — если известен, гарантированно поднимает
// TP минимум до slDist*MinRiskRewardRatio (см. ApplyMinRiskRewardFloor выше).
double CalcTPDistanceMoney(double lot,double targetMoney,double slDist=-1)
{
   double tickValue=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   double tickSize =SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(tickValue<=0 || tickSize<=0 || lot<=0)
      return 50*_Point; // не смогли посчитать — небольшой безопасный fallback

   double priceMove=targetMoney*tickSize/(lot*tickValue);
   double tpPoints=priceMove/_Point;
   double maxPts=EffPointsThreshold(TPMaxPoints,8.0); // п.24: авто под ATR инструмента
   tpPoints=MathMax(1, MathMin(maxPts, tpPoints));
   double tpDist=tpPoints*_Point;
   if(slDist>0) tpDist=ApplyMinRiskRewardFloor(tpDist, slDist);
   return tpDist;
}
