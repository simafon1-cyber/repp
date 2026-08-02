//+------------------------------------------------------------------+
//| MarketContext.mqh                                                |
//| "Понимание всего рынка" (п.19) БЕЗ парсинга внешних сайтов:      |
//| MT5 сам умеет смотреть на любой символ, который есть у брокера — |
//| индекс доллара, другие валюты, крипту. Если наш сигнал совпадает |
//| с трендом связанного инструмента (в правильную сторону           |
//| корреляции) — это независимое подтверждение, score растёт.       |
//| Если явно расходится — небольшой штраф, не жёсткий блок: одна    |
//| связка не должна перекрывать основной сигнал.                    |
//| Требует Config.mqh (хендлы ContextHandle1..3, input-параметры).  |
//+------------------------------------------------------------------+

// Добавляет символ в Market Watch (если ещё не добавлен) и создаёт EMA-хендл.
// Пустая строка — слот просто не используется, это нормальная ситуация.
// Ошибка (символ не найден у брокера) — не валит инициализацию EA целиком,
// контекст по этому символу молча отключается.
bool InitContextSymbol(string sym,int &handleOut)
{
   handleOut=INVALID_HANDLE;
   if(sym=="") return true;

   if(!SymbolSelect(sym,true))
   {
      Print("Контекст рынка: символ '",sym,"' не найден у брокера — слот отключён. ",
            "Проверь точное написание в обзоре рынка (Market Watch).");
      return false;
   }

   handleOut=iMA(sym,TrendTimeframe,ContextEMAPeriod,0,MODE_EMA,PRICE_CLOSE);
   if(handleOut==INVALID_HANDLE)
   {
      Print("Контекст рынка: не удалось создать индикатор для '",sym,"'");
      return false;
   }
   return true;
}

void CreateMarketContext()
{
   if(!UseMarketContext) return;
   InitContextSymbol(ContextSymbol1,ContextHandle1);
   InitContextSymbol(ContextSymbol2,ContextHandle2);
   InitContextSymbol(ContextSymbol3,ContextHandle3);
}

void ReleaseMarketContext()
{
   if(ContextHandle1!=INVALID_HANDLE) IndicatorRelease(ContextHandle1);
   if(ContextHandle2!=INVALID_HANDLE) IndicatorRelease(ContextHandle2);
   if(ContextHandle3!=INVALID_HANDLE) IndicatorRelease(ContextHandle3);
}

// Тренд связанного инструмента: цена относительно его EMA на том же старшем TF,
// что и наш основной MTF-тренд (TrendDirectionMTF в Indicators.mqh) — для единообразия.
int GetContextTrend(string sym,int handle)
{
   if(handle==INVALID_HANDLE || sym=="") return 0;
   double e[]; if(CopyBuffer(handle,0,0,1,e)<=0) return 0;
   double c=iClose(sym,TrendTimeframe,0);
   if(c<=0) return 0;
   if(c>e[0]) return 1;
   if(c<e[0]) return -1;
   return 0;
}

double ContextSlotAdjustment(string sym,int handle,ENUM_CONTEXT_CORRELATION corr,int direction)
{
   int trend=GetContextTrend(sym,handle);
   if(trend==0) return 0; // слот не используется или инструмент сейчас без явного тренда

   // При ПРЯМОЙ корреляции ожидаем тот же тренд, что и наше направление сделки.
   // При ОБРАТНОЙ — противоположный (напр. доллар вниз, когда мы покупаем золото).
   int expected=(corr==CONTEXT_POSITIVE)?direction:-direction;
   if(trend==expected) return ContextScoreWeight;
   return -ContextScoreWeight*0.5; // явное расхождение — мягкий штраф, не запрет входа
}

double MarketContextScoreAdjustment(int direction)
{
   if(!UseMarketContext) return 0;
   double adj=0;
   adj+=ContextSlotAdjustment(ContextSymbol1,ContextHandle1,ContextSymbol1Corr,direction);
   adj+=ContextSlotAdjustment(ContextSymbol2,ContextHandle2,ContextSymbol2Corr,direction);
   adj+=ContextSlotAdjustment(ContextSymbol3,ContextHandle3,ContextSymbol3Corr,direction);
   return adj;
}

string ContextSlotText(string sym,int handle)
{
   if(sym=="") return "";
   int trend=GetContextTrend(sym,handle);
   string t=(trend==1)?"ВВЕРХ":((trend==-1)?"ВНИЗ":"?");
   return sym+":"+t+"  ";
}

string MarketContextText()
{
   if(!UseMarketContext) return "выкл";
   string txt="";
   txt+=ContextSlotText(ContextSymbol1,ContextHandle1);
   txt+=ContextSlotText(ContextSymbol2,ContextHandle2);
   txt+=ContextSlotText(ContextSymbol3,ContextHandle3);
   if(txt=="") return "нет настроенных символов";
   return txt;
}
