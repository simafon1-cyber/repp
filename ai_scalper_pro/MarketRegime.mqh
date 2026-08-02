//+------------------------------------------------------------------+
//| MarketRegime.mqh                                                 |
//| Определение режима рынка: ТРЕНД или ФЛЭТ (п.18).                 |
//| Идея с форумов/статей по алготрейдингу: один и тот же паттерн     |
//| "откат+пробой" хорошо работает в тренде и часто ложный во флэте. |
//| Три независимых голоса (ADX, Kaufman Efficiency Ratio, ATR к      |
//| среднему) — режим определяется большинством, а не одним           |
//| индикатором, чтобы не ловить шум. Смена режима подтверждается     |
//| несколько баров подряд (RegimeConfirmBars) — без этого EA будет   |
//| дёргаться между "тренд/флэт" каждую свечу и толку от адаптации не |
//| будет. Требует Indicators.mqh (ATRHandle, GetADXValue).           |
//+------------------------------------------------------------------+

// Kaufman's Efficiency Ratio: |чистое смещение цены| / сумма |баровых смещений|.
// Ближе к 1 — цена идёт эффективно в одну сторону (тренд).
// Ближе к 0 — цена "топчется", много движения без результата (флэт/чоп).
double GetEfficiencyRatio(int period)
{
   if(period<=0) return 0;
   double closeNow = iClose(_Symbol,_Period,SIGNAL_SHIFT);
   double closeAgo = iClose(_Symbol,_Period,SIGNAL_SHIFT+period);
   double netChange = MathAbs(closeNow-closeAgo);

   double sumChanges=0;
   for(int i=SIGNAL_SHIFT;i<SIGNAL_SHIFT+period;i++)
      sumChanges += MathAbs(iClose(_Symbol,_Period,i)-iClose(_Symbol,_Period,i+1));

   if(sumChanges<=0) return 0;
   return netChange/sumChanges;
}

// "Сырой" режим по 3 голосам, без сглаживания/подтверждения.
ENUM_MARKET_REGIME DetectRawRegime()
{
   int trendVotes=0, rangeVotes=0;

   // Голос 1: ADX
   double adx=GetADXValue(SIGNAL_SHIFT);
   if(adx>=RegimeADXTrendLevel)      trendVotes++;
   else if(adx<RegimeADXRangeLevel)  rangeVotes++;

   // Голос 2: Efficiency Ratio
   double er=GetEfficiencyRatio(RegimeERPeriod);
   if(er>=RegimeERTrendLevel)        trendVotes++;
   else if(er<RegimeERRangeLevel)    rangeVotes++;

   // Голос 3: текущий ATR относительно своего среднего — расширение волатильности
   // чаще сопровождает трендовое движение, сжатие — консолидацию/флэт
   double atrArr[];
   if(CopyBuffer(ATRHandle,0,0,ATRAvgPeriod+1,atrArr)>0)
   {
      double sum=0; for(int i=1;i<=ATRAvgPeriod;i++) sum+=atrArr[i];
      double avg=sum/ATRAvgPeriod;
      if(avg>0)
      {
         double ratio=atrArr[0]/avg;
         if(ratio>=1.2)      trendVotes++;
         else if(ratio<0.85) rangeVotes++;
      }
   }

   if(trendVotes>rangeVotes) return REGIME_TREND;
   if(rangeVotes>trendVotes) return REGIME_RANGE;
   return REGIME_UNKNOWN; // ничья или недостаточно данных — не режим, а "не знаю"
}

// Вызывается ОДИН РАЗ на новый бар (не на каждый тик — режим не может
// меняться быстрее свечи). Требует подтверждения RegimeConfirmBars баров
// подряд, прежде чем реально сменить g_currentRegime — так одиночный шумный
// бар не переключает поведение советника туда-сюда.
void UpdateMarketRegime()
{
   if(!UseMarketRegimeFilter) { g_currentRegime=REGIME_UNKNOWN; return; }

   ENUM_MARKET_REGIME raw=DetectRawRegime();

   if(raw==g_regimeCandidate)
      g_regimeCandidateStreak++;
   else
   {
      g_regimeCandidate=raw;
      g_regimeCandidateStreak=1;
   }

   if(raw!=REGIME_UNKNOWN && g_regimeCandidateStreak>=RegimeConfirmBars)
      g_currentRegime=raw;
   // иначе держим прежнее подтверждённое состояние — защита от дребезга
}

// Корректировка score под текущий подтверждённый режим.
// Флэт: паттерн откат+пробой на этом EA — трендовый по своей природе,
// в чопе он чаще ложный -> штраф. Тренд: паттерн работает штатно -> небольшой бонус.
double RegimeScoreAdjustment()
{
   if(!UseMarketRegimeFilter) return 0;
   if(g_currentRegime==REGIME_RANGE) return -RegimeRangePenalty;
   if(g_currentRegime==REGIME_TREND) return  RegimeTrendBonus;
   return 0;
}

string RegimeText()
{
   if(!UseMarketRegimeFilter) return "выкл";
   switch(g_currentRegime)
   {
      case REGIME_TREND: return "ТРЕНД (+"+DoubleToString(RegimeTrendBonus,0)+" score)";
      case REGIME_RANGE: return "ФЛЭТ (-"+DoubleToString(RegimeRangePenalty,0)+" score)";
      default:            return "определяется...";
   }
}
