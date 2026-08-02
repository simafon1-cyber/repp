//+------------------------------------------------------------------+
//| Dashboard.mqh                                                    |
//| Информационная панель на графике (Comment) + две кнопки          |
//| ("Закрыть прибыльные"/"Закрыть убыточные", по просьбе             |
//| пользователя — то же самое, что уже было сделано в Python-        |
//| программе). Панель только читает состояние из других модулей и   |
//| рисует текст — сама ничего не решает; кнопки вызывают             |
//| CloseProfitablePositions()/CloseLosingPositions() из              |
//| TradeManager.mqh, торговую логику не трогают.                    |
//+------------------------------------------------------------------+

#define AISP_BTN_CLOSE_PROFIT "AISP_BtnCloseProfitable"
#define AISP_BTN_CLOSE_LOSS   "AISP_BtnCloseLosing"

void AISP_CreateButton(string name,string text,int yDist,color bgClr)
{
   if(ObjectFind(0,name)>=0) return; // уже создана (напр. после reload EA на графике)
   ObjectCreate(0,name,OBJ_BUTTON,0,0,0);
   ObjectSetInteger(0,name,OBJPROP_CORNER,CORNER_RIGHT_UPPER);
   ObjectSetInteger(0,name,OBJPROP_XDISTANCE,10);
   ObjectSetInteger(0,name,OBJPROP_YDISTANCE,yDist);
   ObjectSetInteger(0,name,OBJPROP_XSIZE,170);
   ObjectSetInteger(0,name,OBJPROP_YSIZE,26);
   ObjectSetString(0,name,OBJPROP_TEXT,text);
   ObjectSetInteger(0,name,OBJPROP_COLOR,clrWhite);
   ObjectSetInteger(0,name,OBJPROP_BGCOLOR,bgClr);
   ObjectSetInteger(0,name,OBJPROP_BORDER_COLOR,clrBlack);
   ObjectSetInteger(0,name,OBJPROP_FONTSIZE,9);
   ObjectSetInteger(0,name,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,name,OBJPROP_HIDDEN,true);
   ObjectSetInteger(0,name,OBJPROP_ZORDER,100);
}
void CreateDashboardButtons()
{
   if(!ShowDashboard) return;
   AISP_CreateButton(AISP_BTN_CLOSE_PROFIT, "Закрыть прибыльные", 10, clrForestGreen);
   AISP_CreateButton(AISP_BTN_CLOSE_LOSS,   "Закрыть убыточные",  42, clrFireBrick);
}
void RemoveDashboardButtons()
{
   ObjectDelete(0,AISP_BTN_CLOSE_PROFIT);
   ObjectDelete(0,AISP_BTN_CLOSE_LOSS);
}
// Вызывается из OnChartEvent (AI_Scalper_Pro.mq5) на CHARTEVENT_OBJECT_CLICK.
void HandleDashboardButtonClick(string sparam)
{
   if(sparam==AISP_BTN_CLOSE_PROFIT)
   {
      CloseProfitablePositions();
      ObjectSetInteger(0,AISP_BTN_CLOSE_PROFIT,OBJPROP_STATE,false);
      ChartRedraw(0);
   }
   else if(sparam==AISP_BTN_CLOSE_LOSS)
   {
      CloseLosingPositions();
      ObjectSetInteger(0,AISP_BTN_CLOSE_LOSS,OBJPROP_STATE,false);
      ChartRedraw(0);
   }
}

void UpdateDashboard()
{
   if(!ShowDashboard) return;

   // п.16: не пересчитываем и не перерисовываем панель чаще 2 раз/сек — глазу
   // разницы нет, а на быстром тикающем инструменте (типа XAUUSD) это заметно
   // снижает нагрузку на каждый OnTick.
   ulong nowMs=GetTickCount64();
   if(g_lastDashboardMs!=0 && nowMs-g_lastDashboardMs<500) return;
   g_lastDashboardMs=nowMs;

   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   double dayPct=(DayStartEquity>0)?(equity-DayStartEquity)/DayStartEquity*100.0:0;
   double ddPct=(g_peakEquity>0)?(g_peakEquity-equity)/g_peakEquity*100.0:0;
   int mtf=TrendDirectionMTF();
   string trendTxt=(mtf==1)?"ВВЕРХ":((mtf==-1)?"ВНИЗ":"ФЛЭТ");

   double winRate = (g_totalTrades>0) ? (double)g_winTrades/g_totalTrades*100.0 : 0;
   double pf = (g_grossLoss<0) ? g_grossProfit/MathAbs(g_grossLoss) : 0;

   string status="АКТИВЕН";
   if(DailyLossLimitHit())       status="ОСТАНОВЛЕН (дневной убыток)";
   else if(MaxDrawdownHit())     status="ОСТАНОВЛЕН (просадка)";
   else if(LossStreakPauseActive()) status="ПАУЗА (серия убытков, до "+TimeToString(g_pauseUntil,TIME_DATE|TIME_MINUTES)+")";

   string tpModeTxt;
   if(UseMaxProfitRide)
      tpModeTxt="макс. профит (без TP, только BE/трейлинг/Profit Lock)";
   else if(g_effUseMoneyTP)
      tpModeTxt="деньги, "+DoubleToString(g_effTargetProfitMoney,2)+" "+AccountInfoString(ACCOUNT_CURRENCY)+"/сделка (мин. R:R x"+DoubleToString(MinRiskRewardRatio,1)+")";
   else
      tpModeTxt="RR "+DoubleToString(RiskRewardRatio,1)+" (мин. R:R x"+DoubleToString(MinRiskRewardRatio,1)+")";

   string modeTxt=(TradingMode==MODE_SCALPING)?"Скальпинг":((TradingMode==MODE_NEWS_TRADING)?"Новости":"Скальпинг+Новости");

   string txt="";
   txt+="=== AI Scalper Pro v8.0 ===\n";
   txt+="Профиль: "+g_activeProfileName+" | Риск/сделка: "+DoubleToString(g_effRiskPercent,2)+"%\n";
   txt+="Символ: "+_Symbol+"  ТФ: "+EnumToString(_Period)+" | Тренд ТФ: "+EnumToString(TrendTimeframe)+"\n";
   txt+="Режим торговли: "+modeTxt+" | Автонастройка под инструмент: "+(AutoAdaptToSymbol?"ВКЛ":"ВЫКЛ")+"\n";
   txt+="Тренд (старший ТФ): "+trendTxt+" | Режим рынка: "+RegimeText()+"\n";
   if(UseMarketContext)
      txt+="Контекст рынка: "+MarketContextText()+"\n";
   txt+="Спред ОК: "+(SpreadOK()?"да":"нет")+" ("+(string)SymbolInfoInteger(_Symbol,SYMBOL_SPREAD)+" пт)\n";
   txt+="Фильтр объёма: "+(UseVolumeFilter?"ВКЛ":"ВЫКЛ (мягкий)")+"\n";
   txt+="ATR: "+DoubleToString(GetATRValue()/_Point,1)+" пт | ADX: "+DoubleToString(GetADXValue(),1)+" | RSI: "+DoubleToString(GetRSIValue(),1)+"\n";
   txt+="Открытых сделок: "+(string)CountOpenPositions()+"/"+(string)g_effMaxOpenPositions+" | Сделок сегодня: "+(string)TradesToday+"/"+(string)g_effMaxTradesPerDay+"\n";
   txt+="Общий риск открытых сделок: "+DoubleToString(GetOpenRiskPercent(),2)+"% / потолок "+DoubleToString(g_effMaxTotalRiskPercent,1)+"%\n";
   txt+="Режим TP: "+tpModeTxt+"\n";
   double curPts=0,peakPts=0;
   if(GetOwnPositionProfitInfo(curPts,peakPts))
      txt+="Позиция: "+DoubleToString(curPts,0)+" пт | Пик: "+DoubleToString(peakPts,0)+" пт | Лок: "+DoubleToString(ProfitLockPercent,0)+"%\n";
   txt+="P/L за день: "+DoubleToString(dayPct,2)+"% | Просадка: "+DoubleToString(ddPct,2)+"%\n";
   txt+="Винрейт: "+DoubleToString(winRate,1)+"% ("+(string)g_winTrades+"/"+(string)g_totalTrades+")\n";
   txt+="Профит-фактор: "+DoubleToString(pf,2)+"\n";
   txt+="Score: BUY "+DoubleToString(g_lastBuyScore,1)+" / SELL "+DoubleToString(g_lastSellScore,1)+" (порог "+DoubleToString(g_effMinScoreToTrade,0)+")\n";
   txt+="Адаптивные веса score: "+(UseAdaptiveScoreWeights?"ВКЛ":"ВЫКЛ")+"\n";
   if(UseCustomStrategy)
      txt+="Своя стратегия (v"+CUSTOM_STRATEGY_VERSION+"): "+DoubleToString(g_lastCustomScore,1)+" (вес "+DoubleToString(CustomStrategyWeight,0)+")\n";
   if(UseMultiIndicator)
      txt+="Доп. индикаторы MACD/BB/Stoch (v"+MULTI_INDICATOR_VERSION+"): "+DoubleToString(g_lastMultiIndicatorScore,1)+" (вес "+DoubleToString(MultiIndicatorWeight,0)+")\n";
   if(UseExternalSignal)
      txt+="Внешний сигнал: "+(g_extLastOk ? (g_extLastDirection+" ("+DoubleToString(g_extLastConfidence*100,0)+"%), вес "+DoubleToString(EffectiveExternalSignalWeight(),1)) : "недоступен")+"\n";
   txt+="Причина отказа: "+g_lastRejectReason+"\n";
   txt+="Последняя сделка: "+g_lastTradeResult+"\n";
   txt+="Серия убытков: "+(string)g_consecutiveLosses+"/"+(string)MaxConsecutiveLosses+
        " (риск x"+DoubleToString(GetLossStreakRiskMultiplier(),2)+")\n";
   txt+="Статус: "+status;
   Comment(txt);
}
