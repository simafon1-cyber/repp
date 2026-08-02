//+------------------------------------------------------------------+
//| CustomStrategy.mqh                                               |
//| "Proprietary Edge": СОБСТВЕННАЯ стратегия/сигнал советника —      |
//| портировано 1:1 из custom_strategy.py (Python-программа), по      |
//| просьбе пользователя ("сделай то же самое для советника").        |
//|                                                                    |
//| Смысл: не заменяет существующий score (SignalEngine.mqh), а даёт  |
//| ВТОРОЕ, независимое мнение по 4 факторам — импульс, ускорение      |
//| импульса, направленная согласованность последних баров и          |
//| расширение диапазона свечей. Мнение подмешивается в общий score с |
//| ограниченным весом (как внешний AI-сигнал, см. CustomStrategyWeight|
//| в Config.mqh) — не жёсткая команда, а мягкая добавка.              |
//|                                                                    |
//| ОБНОВЛЯЕМОСТЬ: формулы/веса вынесены в именованные константы и    |
//| версионируются здесь же (CUSTOM_STRATEGY_VERSION + CHANGELOG) —   |
//| чтобы менять логику со временем было легко. Правь константы/      |
//| формулы ниже и добавляй запись в CHANGELOG.                        |
//|                                                                    |
//| CHANGELOG:                                                         |
//|   v1.0 (первая версия, портирована из custom_strategy.py) — 4      |
//|   фактора по 25 баллов: Momentum Thrust, Momentum Acceleration,    |
//|   Directional Consistency, Range Expansion.                       |
//|                                                                    |
//| Требует Config.mqh (SIGNAL_SHIFT) и Indicators.mqh (GetATRValue). |
//+------------------------------------------------------------------+

#define CUSTOM_STRATEGY_VERSION "1.0"

// ---- Настраиваемые параметры (меняй здесь при "обновлении" стратегии) -----
#define CS_MOMENTUM_LOOKBACK     5    // баров назад для Momentum Thrust
#define CS_CONSISTENCY_LOOKBACK  8    // баров для Directional Consistency
#define CS_RANGE_LOOKBACK        10   // баров для среднего диапазона (Range Expansion)

// Масштаб перевода "движение / ATR" в баллы (0..25) — подобраны эмпирически,
// как отправная точка; при обновлении стратегии их можно свободно менять.
#define CS_MOMENTUM_SCALE        12.0
#define CS_ACCELERATION_SCALE    20.0
#define CS_RANGE_EXPANSION_SCALE 15.0

double CS_Clamp(double value,double lo=0.0,double hi=25.0)
{
   return MathMax(lo, MathMin(hi, value));
}

// 0..25: сила недавнего направленного движения, нормированная по ATR — чем
// сильнее цена уже прошла в сторону сделки за CS_MOMENTUM_LOOKBACK баров, тем
// больше баллов (ловим "свежий" импульс, а не гадаем на пустом месте).
double CS_MomentumThrust(int direction,double atrValue)
{
   int n=CS_MOMENTUM_LOOKBACK;
   if(atrValue<=0) return 0.0;
   double closeNow = iClose(_Symbol,_Period,SIGNAL_SHIFT);
   double closeAgo = iClose(_Symbol,_Period,SIGNAL_SHIFT+n);
   double roc = (closeNow-closeAgo)/atrValue;
   double aligned = (direction==1) ? roc : -roc;
   return CS_Clamp(aligned*CS_MOMENTUM_SCALE);
}

// 0..25: РАЗГОНЯЕТСЯ ли движение (вторая производная цены), а не просто
// существует — отличает начало движения (баллы растут) от уже уставшего,
// затухающего хода (баллы падают к нулю), даже если сам импульс ещё виден
// в CS_MomentumThrust выше.
double CS_MomentumAcceleration(int direction,double atrValue)
{
   if(atrValue<=0) return 0.0;
   double c0=iClose(_Symbol,_Period,SIGNAL_SHIFT);
   double c1=iClose(_Symbol,_Period,SIGNAL_SHIFT+1);
   double c2=iClose(_Symbol,_Period,SIGNAL_SHIFT+2);
   double rocNow  = c0-c1;
   double rocPrev = c1-c2;
   double accel = (rocNow-rocPrev)/atrValue;
   double aligned = (direction==1) ? accel : -accel;
   return CS_Clamp(aligned*CS_ACCELERATION_SCALE);
}

// 0..25: какая доля последних CS_CONSISTENCY_LOOKBACK баров закрылась в
// сторону сделки — высокая согласованность движения снижает шанс, что это
// случайный/шумовой всплеск на одной свече.
double CS_DirectionalConsistency(int direction)
{
   int n=CS_CONSISTENCY_LOOKBACK;
   int alignedCount=0;
   for(int i=SIGNAL_SHIFT;i<SIGNAL_SHIFT+n;i++)
   {
      double diff = iClose(_Symbol,_Period,i) - iClose(_Symbol,_Period,i+1);
      bool aligned = (direction==1) ? (diff>0) : (diff<0);
      if(aligned) alignedCount++;
   }
   double fraction=(double)alignedCount/(double)n;
   return CS_Clamp(fraction*25.0);
}

// 0..25: расширяется ли диапазон последней свечи относительно недавнего
// среднего — признак свежего интереса/продолжения пробоя, а не вязкого
// "растирания" внутри узкого диапазона.
double CS_RangeExpansion()
{
   int n=CS_RANGE_LOOKBACK;
   double current = iHigh(_Symbol,_Period,SIGNAL_SHIFT) - iLow(_Symbol,_Period,SIGNAL_SHIFT);
   double sum=0;
   for(int i=SIGNAL_SHIFT+1;i<=SIGNAL_SHIFT+n;i++)
      sum += (iHigh(_Symbol,_Period,i) - iLow(_Symbol,_Period,i));
   double avg=sum/n;
   if(avg<=0) return 0.0;
   double ratio=current/avg;
   return CS_Clamp((ratio-1.0)*CS_RANGE_EXPANSION_SCALE + 5.0);
}

// Итоговый 0..100 score этой стратегии для направления direction (1=BUY, -1=SELL).
double CalcCustomScore(int direction)
{
   if(!UseCustomStrategy) return 50.0; // нейтрально — ApplyCustomStrategy() всё равно даст 0 влияния

   double atrValue=GetATRValue(SIGNAL_SHIFT);
   double total = CS_MomentumThrust(direction,atrValue)
                + CS_MomentumAcceleration(direction,atrValue)
                + CS_DirectionalConsistency(direction)
                + CS_RangeExpansion();
   return NormalizeDouble(MathMax(0.0, MathMin(100.0, total)), 1);
}

// Подмешивает customScore (0..100) в основной score с ограниченным весом:
// customScore=50 (нейтрально) -> без изменений; 100 -> +weight; 0 -> -weight.
// Как ApplyExternalSignal() в NewsAI.mqh — мягкая добавка, а не самостоятельная команда.
// weight<0 (по умолчанию) значит "взять CustomStrategyWeight из Config.mqh".
double ApplyCustomStrategy(double score,double customScore,double weight=-1.0)
{
   if(!UseCustomStrategy) return score;
   double w = (weight>=0) ? weight : CustomStrategyWeight;
   double delta = (customScore/100.0 - 0.5)*2.0*w;
   return MathMax(0.0, MathMin(100.0, score+delta));
}
