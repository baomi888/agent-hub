package com.zhisui.app.domain.model

/** 用量配额。数据形状对齐后端 `/api/auth/me` 返回的 quota 快照。 */
data class UsageQuota(
    val usedTokens: Long,
    val limitTokens: Long,
    val resetLabel: String,
    val plan: String,
) {
    val ratio: Float
        get() = if (limitTokens <= 0L) 0f else (usedTokens.toFloat() / limitTokens).coerceIn(0f, 1f)

    val remaining: Long get() = (limitTokens - usedTokens).coerceAtLeast(0L)

    /** 保留一位小数的万单位显示，避免 128400 这种长数字挤坏布局。 */
    val usedLabel: String get() = formatTokens(usedTokens)
    val limitLabel: String get() = formatTokens(limitTokens)
    val remainingLabel: String get() = formatTokens(remaining)

    companion object {
        fun formatTokens(value: Long): String = when {
            value >= 10_000L -> trimZero(value / 1_000L / 10.0) + "万"
            else -> value.toString()
        }

        private fun trimZero(v: Double): String =
            if (v == v.toLong().toDouble()) v.toLong().toString() else String.format("%.1f", v)
    }
}
