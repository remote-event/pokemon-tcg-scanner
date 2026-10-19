package dev.tcg.scanner

data class Card(val ordinal:Int,val id:String,val name:String,val set:String,
                val number:String,val code:String?,val total:Int?,val imageUrl:String)
data class Match(val card:Card,val similarity:Float)
data class Decision(val matches:List<Match>,val status:String,val verified:Boolean)
