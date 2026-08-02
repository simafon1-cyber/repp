//+------------------------------------------------------------------+
//| Indicators.mqh                                                   |
//| Создание индикаторов, чтение их значений, тренд старшего TF,     |
//| price action и pullback-паттерн. Всё, что "смотрит на график".   |
//| Требует Config.mqh (хендлы и input уже объявлены там).           |
//+------------------------------------------------------------------+

//===================== СОЗДАНИЕ / ОСВОБОЖДЕНИЕ ИНДИКАТОРОВ =========
bool CreateIndicators()
{
   EMAFastHandle  = iMA(_Symbol,_Period,EMAFastPeriod,0,MODE_EMA,PRICE_CLOSE);
   EMASlowHandle  = iMA(_Symbol,_Period,EMASlowPeriod,0,MODE_EMA,PRICE_CLOSE);
   EMATrendHandle = iMA(_Symbol,TrendTimeframe,EMATrendPeriod,0,MODE_EMA,PRICE_CLOSE); // MTF!
   ATRHandle      = iATR(_Symbol,_Period,ATRPeriod);
   ADXHandle      = iADX(_Symbol,_Period,ADXPeriod);
   RSIHandle      = iRSI(_Symbol,_Period,RSIPeriod,PRICE_CLOSE);

   if(EMAFastHandle==INVALID_HANDLE || EMASlowHandle==INVALID_HANDLE ||
      EMATrendHandle==INVALID_HANDLE || ATRHandle==INVALID_HANDLE ||
      ADXHandle==INVALID_HANDLE || RSIHandle==INVALID_HANDLE)
      return false;

   if(TrendTimeframe <= _Period)
      Print("Внимание: TrendTimeframe (", EnumToString(TrendTimeframe),
            ") не старше текущего графика (", EnumToString(_Period), "). MTF-логика теряет смысл.");

   return true;
}

void ReleaseIndicators()
{
   if(EMAFastHandle!=INVALID_HANDLE)  IndicatorRelease(EMAFastHandle);
   if(EMASlowHandle!=INVALID_HANDLE)  IndicatorRelease(EMASlowHandle);
   if(EMATrendHandle!=INVALID_HANDLE) IndicatorRelease(EMATrendHandle);
   if(ATRHandle!=INVALID_HANDLE)      IndicatorRelease(ATRHandle);
   if(ADXHandle!=INVALID_HANDLE)      IndicatorRelease(ADXHandle);
   if(RSIHandle!=INVALID_HANDLE)      IndicatorRelease(RSIHandle);
}

//===================== ИНДИКАТОРНЫЕ ЗНАЧЕНИЯ ===========
double GetADXValue(int shift=0){ double a[]; if(CopyBuffer(ADXHandle,0,shift,1,a)<=0) return 0; return a[0]; }
double GetRSIValue(int shift=0){ double r[]; if(CopyBuffer(RSIHandle,0,shift,1,r)<=0) return 50; return r[0]; }
double GetATRValue(int shift=0){ double a[]; if(CopyBuffer(ATRHandle,0,shift,1,a)<=0) return 0; return a[0]; }

//===================== АВТОНАСТРОЙКА ПОД ИНСТРУМЕНТ (п.24) ============
// См. комментарий в Config.mqh рядом с AutoAdaptToSymbol. Определены здесь
// (а не в RiskManager.mqh), потому что PullbackBreakoutOk в этом же файле
// уже нужен этот порог, а инклюды в .mq5 идут строго по порядку "определение
// до использования" — Indicators.mqh подключается раньше RiskManager.mqh и
// TradeManager.mqh, которые тоже используют эту функцию.
double AutoRefATRPoints()
{
   double atr=GetATRValue();
   if(atr<=0) return 0;
   return atr/_Point;
}
double EffPointsThreshold(double manualPoints,double atrFraction)
{
   if(!AutoAdaptToSymbol) return manualPoints;
   double refAtrPts=AutoRefATRPoints();
   if(refAtrPts<=0) return manualPoints; // индикатор ещё не готов — fallback на ручное значение
   return refAtrPts*atrFraction;
}

// направление тренда СТАРШЕГО TF (для дашборда и score)
int TrendDirectionMTF()
{
   double t[]; if(CopyBuffer(EMATrendHandle,0,0,1,t)<=0) return 0;
   double c=iClose(_Symbol,_Period,SIGNAL_SHIFT);
   if(c>t[0]) return 1;
   if(c<t[0]) return -1;
   return 0;
}

//===================== PRICE ACTION ====================
bool IsBullishConfirmation(int shift)
{
   double o=iOpen(_Symbol,_Period,shift), c=iClose(_Symbol,_Period,shift);
   double h=iHigh(_Symbol,_Period,shift), l=iLow(_Symbol,_Period,shift);
   double range=h-l; if(range<=0) return false;
   if(c<=o) return false;
   double body=c-o, upperWick=h-c;
   if(body/range*100.0<BodyPercentMin) return false;
   if(upperWick/range*100.0>MaxWickPercent) return false;
   return true;
}
bool IsBearishConfirmation(int shift)
{
   double o=iOpen(_Symbol,_Period,shift), c=iClose(_Symbol,_Period,shift);
   double h=iHigh(_Symbol,_Period,shift), l=iLow(_Symbol,_Period,shift);
   double range=h-l; if(range<=0) return false;
   if(c>=o) return false;
   double body=o-c, lowerWick=c-l;
   if(body/range*100.0<BodyPercentMin) return false;
   if(lowerWick/range*100.0>MaxWickPercent) return false;
   return true;
}

//===================== PULLBACK + ПРОБОЙ (п.2: уточнено под 3-шаговую схему) =
// Шаг 1: предыдущая свеча (PULLBACK_SHIFT) коснулась EMA20.
// Шаг 2: текущая сигнальная свеча (SIGNAL_SHIFT) ЗАКРЫЛАСЬ выше EMA20 (для buy).
// Шаг 3: текущая свеча обновила максимум предыдущей свечи.
bool PullbackBreakoutOk(int direction)
{
   double emaFastPB[]; if(CopyBuffer(EMAFastHandle,0,PULLBACK_SHIFT,1,emaFastPB)<=0) return false;
   double lowPB  =iLow(_Symbol,_Period,PULLBACK_SHIFT);
   double highPB =iHigh(_Symbol,_Period,PULLBACK_SHIFT);
   double tol=EffPointsThreshold(PullbackTolerancePoints,0.10)*_Point; // п.24: авто под ATR инструмента

   double closeSig=iClose(_Symbol,_Period,SIGNAL_SHIFT);
   double highSig =iHigh(_Symbol,_Period,SIGNAL_SHIFT);
   double lowSig  =iLow(_Symbol,_Period,SIGNAL_SHIFT);

   if(direction==1)
   {
      bool touchedEMA    = lowPB <= emaFastPB[0]+tol;        // шаг 1
      bool closedAboveEMA = closeSig > emaFastPB[0];          // шаг 2
      bool brokeHigh      = highSig > highPB;                 // шаг 3
      return touchedEMA && closedAboveEMA && brokeHigh;
   }
   else
   {
      bool touchedEMA    = highPB >= emaFastPB[0]-tol;
      bool closedBelowEMA = closeSig < emaFastPB[0];
      bool brokeLow        = lowSig < lowPB;
      return touchedEMA && closedBelowEMA && brokeLow;
   }
}

bool EMAStackOk(int direction)
{
   double f[],s[];
   if(CopyBuffer(EMAFastHandle,0,SIGNAL_SHIFT,1,f)<=0) return false;
   if(CopyBuffer(EMASlowHandle,0,SIGNAL_SHIFT,1,s)<=0) return false;
   return (direction==1) ? (f[0]>s[0]) : (f[0]<s[0]);
}
