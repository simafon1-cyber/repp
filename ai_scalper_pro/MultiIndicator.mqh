//+------------------------------------------------------------------+
//| MultiIndicator.mqh                                               |
//| Дополнительное подтверждение сигнала тремя классическими          |
//| индикаторами (MACD, Bollinger Bands, Stochastic) — по просьбе     |
//| пользователя "используй как можно больше индикаторов и торговых   |
//| стратегий". Как CustomStrategy.mqh — НЕ заменяет основной score   |
//| (SignalEngine.mqh), а даёт ЕЩЁ ОДНО независимое мнение,           |
//| подмешивается с ограниченным весом (MultiIndicatorWeight).        |
//|                                                                    |
//| CHANGELOG:                                                         |
//|   v1.0 — 3 индикатора: MACD (0..40), Bollinger %B (0..30),         |
//|   Stochastic %K/%D разворот (0..30).                               |
//|                                                                    |
//| Требует Config.mqh (хендлы/input объявлены там).                  |
//+------------------------------------------------------------------+

#define MULTI_INDICATOR_VERSION "1.0"

bool CreateMultiIndicatorHandles()
{
   if(!UseMultiIndicator) return true; // не используется — не создаём хендлы зря
   MACDHandle  = iMACD(_Symbol,_Period,MACDFastPeriod,MACDSlowPeriod,MACDSignalPeriod,PRICE_CLOSE);
   BandsHandle = iBands(_Symbol,_Period,BBPeriod,0,BBDeviation,PRICE_CLOSE);
   StochHandle = iStochastic(_Symbol,_Period,StochKPeriod,StochDPeriod,StochSlowing,MODE_SMA,STO_LOWHIGH);
   if(MACDHandle==INVALID_HANDLE || BandsHandle==INVALID_HANDLE || StochHandle==INVALID_HANDLE)
   {
      Print("MultiIndicator: не удалось создать один из хендлов (MACD/Bands/Stochastic) — доп. индикаторы отключены для этого запуска.");
      return false;
   }
   return true;
}

void ReleaseMultiIndicatorHandles()
{
   if(MACDHandle!=INVALID_HANDLE)  IndicatorRelease(MACDHandle);
   if(BandsHandle!=INVALID_HANDLE) IndicatorRelease(BandsHandle);
   if(StochHandle!=INVALID_HANDLE) IndicatorRelease(StochHandle);
}

// 0..40: согласован ли MACD с направлением сделки — линия MACD выше/ниже
// сигнальной линии (+25) И гистограмма того же знака (импульс усиливается, +15).
double MI_MacdFactor(int direction)
{
   double macdArr[], signalArr[];
   if(CopyBuffer(MACDHandle,0,SIGNAL_SHIFT,1,macdArr)<=0) return 0;
   if(CopyBuffer(MACDHandle,1,SIGNAL_SHIFT,1,signalArr)<=0) return 0;
   double macdLine=macdArr[0], signalLine=signalArr[0];
   double hist=macdLine-signalLine;
   bool alignedCross=(direction==1)?(macdLine>signalLine):(macdLine<signalLine);
   bool alignedHist =(direction==1)?(hist>0):(hist<0);
   double score=0;
   if(alignedCross) score+=25.0;
   if(alignedHist)  score+=15.0;
   return score;
}

// 0..30: позиция цены внутри полос Боллинджера (%B). Движение В СТОРОНУ
// сделки, но НЕ у самого края канала (риск разворота у экстремума).
double MI_BollingerFactor(int direction)
{
   double upperArr[], lowerArr[];
   if(CopyBuffer(BandsHandle,1,SIGNAL_SHIFT,1,upperArr)<=0) return 0; // UPPER_BAND=1
   if(CopyBuffer(BandsHandle,2,SIGNAL_SHIFT,1,lowerArr)<=0) return 0; // LOWER_BAND=2
   double upper=upperArr[0], lower=lowerArr[0];
   double width=upper-lower;
   if(width<=0) return 0;
   double close=iClose(_Symbol,_Period,SIGNAL_SHIFT);
   double percentB=(close-lower)/width;
   if(direction==1)
   {
      if(percentB>=0.5 && percentB<=0.95) return 30.0;
      if(percentB>0.95) return 5.0;
      return 0.0;
   }
   else
   {
      if(percentB>=0.05 && percentB<=0.5) return 30.0;
      if(percentB<0.05) return 5.0;
      return 0.0;
   }
}

// 0..30: классический разворотный сигнал — %K пересекает %D, выходя из зоны
// перепроданности (BUY) или перекупленности (SELL).
double MI_StochasticFactor(int direction)
{
   double kArr[], dArr[];
   if(CopyBuffer(StochHandle,0,SIGNAL_SHIFT,1,kArr)<=0) return 0; // MAIN_LINE=0 (%K)
   if(CopyBuffer(StochHandle,1,SIGNAL_SHIFT,1,dArr)<=0) return 0; // SIGNAL_LINE=1 (%D)
   double k=kArr[0], d=dArr[0];
   if(direction==1)
   {
      if(k>d && k<80) return (k<50)?30.0:15.0;
      return 0.0;
   }
   else
   {
      if(k<d && k>20) return (k>50)?30.0:15.0;
      return 0.0;
   }
}

// Итоговый 0..100 score для направления direction (1=BUY, -1=SELL).
double CalcMultiIndicatorScore(int direction)
{
   if(!UseMultiIndicator) return 50.0; // нейтрально — ApplyMultiIndicator() всё равно даст 0 влияния
   double total=MI_MacdFactor(direction)+MI_BollingerFactor(direction)+MI_StochasticFactor(direction);
   return NormalizeDouble(MathMax(0.0,MathMin(100.0,total)),1);
}

// Подмешивает miScore (0..100) в основной score с ограниченным весом:
// miScore=50 (нейтрально) -> без изменений; 100 -> +weight; 0 -> -weight.
double ApplyMultiIndicator(double score,double miScore,double weight=-1.0)
{
   if(!UseMultiIndicator) return score;
   double w=(weight>=0)?weight:MultiIndicatorWeight;
   double delta=(miScore/100.0-0.5)*2.0*w;
   return MathMax(0.0,MathMin(100.0,score+delta));
}
