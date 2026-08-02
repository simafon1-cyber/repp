//+------------------------------------------------------------------+
//| SignalEngine.mqh                                                 |
//| Единый score — сводит тренд, паттерн, price action, волатильность|
//| и новости в одно число 0..100. Требует Indicators.mqh и NewsAI.mqh|
//+------------------------------------------------------------------+

//===================== "УМНЕЕ" SCORE ПОД РЕЖИМ РЫНКА =====
// По просьбе пользователя (портировано из Python USE_ADAPTIVE_SCORE_WEIGHTS,
// см. UseAdaptiveScoreWeights в Config.mqh): вместо фиксированных весов —
// подстройка под уже посчитанный режим рынка (g_currentRegime, MarketRegime.mqh).
// kind="trend_structure" — тренд со старшего ТФ + паттерн pullback+breakout,
// надёжнее в подтверждённом ТРЕНДЕ. kind="mean_reversion" — RSI запас хода,
// надёжнее во ФЛЭТЕ. ВЫКЛ (UseAdaptiveScoreWeights=false) -> всегда 1.0, веса
// как в исходной версии.
double AdaptiveMultiplier(string kind)
{
   if(!UseAdaptiveScoreWeights) return 1.0;
   if(kind=="trend_structure")
   {
      if(g_currentRegime==REGIME_TREND) return 1.15;
      if(g_currentRegime==REGIME_RANGE) return 0.75;
      return 1.0;
   }
   if(kind=="mean_reversion")
   {
      if(g_currentRegime==REGIME_RANGE) return 1.6;
      if(g_currentRegime==REGIME_TREND) return 0.7;
      return 1.0;
   }
   return 1.0;
}

//===================== АНТИ-"ЗЕРКАЛО" ФИЛЬТР #3: СВЕЧА УЖЕ РАСТЯНУТА =====
// По жалобе пользователя ("бот открывает в зеркало" — входит и рынок сразу
// разворачивается): если диапазон сигнальной свечи намного больше среднего
// ATR — движение, скорее всего, уже во многом прошло/истощилось, именно такие
// свечи чаще всего разворачиваются сразу после входа.
bool CandleOverextended()
{
   if(!UseExhaustionFilter) return false;
   double atrArr[];
   if(CopyBuffer(ATRHandle,0,0,ATRAvgPeriod+1,atrArr)<=0) return false;
   double sum=0; for(int i=1;i<=ATRAvgPeriod;i++) sum+=atrArr[i];
   double avgAtr=sum/ATRAvgPeriod;
   if(avgAtr<=0) return false;
   double candleRange=iHigh(_Symbol,_Period,SIGNAL_SHIFT)-iLow(_Symbol,_Period,SIGNAL_SHIFT);
   return (candleRange/avgAtr) > ExhaustionRangeATRRatio;
}

//===================== SCORE — ЕДИНЫЙ ФИЛЬТР (п.6,10) ===
// Веса: Trend20 Pullback20 PriceAction15 ATR10 ADX10 Spread5 Volume10 Time5 RSI5 = 100
double CalcSignalScore(int direction)
{
   // Анти-"зеркало" фильтр #1: Price Action подтверждение (сильное тело,
   // маленькая противоположная тень) теперь ЖЁСТКОЕ условие входа, а не просто
   // бонус +15, как было раньше — без него сделки в эту сторону не будет,
   // независимо от остальных компонентов score. UsePAHardGate=false вернёт
   // старое поведение (просто бонус).
   bool paOk=(direction==1)?IsBullishConfirmation(SIGNAL_SHIFT):IsBearishConfirmation(SIGNAL_SHIFT);
   if(!paOk && UsePAHardGate) return 0;

   // Анти-"зеркало" фильтр #3: свеча уже слишком растянута относительно ATR.
   if(CandleOverextended()) return 0;

   double score=0;
   double trendMult=AdaptiveMultiplier("trend_structure");

   // Trend со старшего TF (20, адаптируется под режим рынка)
   if(EMAStackOk(direction))
   {
      int mtf=TrendDirectionMTF();
      if((direction==1 && mtf==1) || (direction==-1 && mtf==-1)) score+=20*trendMult;
      else if(mtf==0) score+=10*trendMult; // старший TF нейтрален — частичный балл
   }

   // Pullback + пробой (20, тот же адаптивный множитель — тоже "структурный" сигнал)
   if(PullbackBreakoutOk(direction)) score+=20*trendMult;

   // Price Action на сигнальной свече (15) — подтверждение уже гарантировано
   // выше при UsePAHardGate=true, бонус начисляется всегда в этом случае.
   if(paOk) score+=15;

   // ATR относительно среднего (10)
   double atrArr[];
   if(CopyBuffer(ATRHandle,0,0,ATRAvgPeriod+1,atrArr)>0)
   {
      double sum=0; for(int i=1;i<=ATRAvgPeriod;i++) sum+=atrArr[i];
      double avg=sum/ATRAvgPeriod;
      if(avg>0) score+=MathMin(10,(atrArr[0]/avg)*10*0.7);
   }

   // ADX (10)
   double adx=GetADXValue(SIGNAL_SHIFT);
   score+=MathMin(10,(adx/MathMax(ADXMinLevel,1))*10*0.6);

   // Spread (5) — чем уже, тем выше балл
   long spread=SymbolInfoInteger(_Symbol,SYMBOL_SPREAD);
   score+=MathMax(0,MathMin(5,(1.0-(double)spread/MathMax(MaxSpreadPoints,1))*5));

   // Volume (10) — мягкий: если фильтр выключен, баллы не теряются (п.2)
   if(UseVolumeFilter)
   {
      long volArr[];
      if(CopyTickVolume(_Symbol,_Period,SIGNAL_SHIFT,VolumeAvgPeriod+1,volArr)>0)
      {
         long sum=0; for(int i=1;i<=VolumeAvgPeriod;i++) sum+=volArr[i];
         double avg=(double)sum/VolumeAvgPeriod;
         if(avg>0) score+=MathMin(10,((double)volArr[0]/avg)*10*0.7);
      }
   }
   else score+=10;

   // Time (5)
   score+=TimeFilterOk()?5:0;

   // RSI — запас хода (5, адаптируется под режим рынка — усиливается во флэте)
   double rsi=GetRSIValue(SIGNAL_SHIFT);
   double room=(direction==1)?(RSIOverbought-rsi):(rsi-RSIOversold);
   score+=MathMax(0,MathMin(5,room/10.0*5))*AdaptiveMultiplier("mean_reversion");

   // Мягкий штраф за MODERATE-новость рядом (п.11) — не блокирует, просто снижает score
   score-=NewsSoftPenalty();

   // Адаптация под режим рынка (п.18): флэт штрафует трендовый паттерн, тренд немного поощряет
   score+=RegimeScoreAdjustment();

   // Контекст всего рынка (п.19): совпадение с трендом связанных инструментов
   // (индекс доллара, крипта и т.п.) — независимое подтверждение или предупреждение
   score+=MarketContextScoreAdjustment(direction);

   return NormalizeDouble(MathMax(0,MathMin(100,score)),1);
}
