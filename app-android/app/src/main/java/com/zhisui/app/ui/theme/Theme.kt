package com.zhisui.app.ui.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat

enum class ThemeMode(val label: String) {
    DARK("深色"),
    LIGHT("浅色"),
    SYSTEM("跟随系统"),
}

private val DarkColors = darkColorScheme(
    primary = Color(0xFF3FD6A8),
    onPrimary = Color(0xFF04241A),
    primaryContainer = Color(0xFF153F35),
    onPrimaryContainer = Color(0xFFB6EEDC),
    secondary = Color(0xFF9C98F0),
    onSecondary = Color(0xFF14122F),
    secondaryContainer = Color(0xFF272450),
    onSecondaryContainer = Color(0xFFDDDAFB),
    background = Color(0xFF0B0E0F),
    onBackground = Color(0xFFE3EDE8),
    surface = Color(0xFF0B0E0F),
    onSurface = Color(0xFFE3EDE8),
    surfaceVariant = Color(0xFF171C1D),
    onSurfaceVariant = Color(0xFF94A6A1),
    outline = Color(0xFF2A3234),
    outlineVariant = Color(0xFF1D2425),
    error = Color(0xFFF09595),
)

private val LightColors = lightColorScheme(
    primary = Color(0xFF0E8A69),
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFC8F2E2),
    onPrimaryContainer = Color(0xFF04291F),
    secondary = Color(0xFF5B57C8),
    onSecondary = Color(0xFFFFFFFF),
    secondaryContainer = Color(0xFFE2E0FB),
    onSecondaryContainer = Color(0xFF15123A),
    background = Color(0xFFF5F9F7),
    onBackground = Color(0xFF131F1A),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF131F1A),
    surfaceVariant = Color(0xFFECF2EF),
    onSurfaceVariant = Color(0xFF54635D),
    outline = Color(0xFFD4DFDA),
    outlineVariant = Color(0xFFE3ECE8),
    error = Color(0xFFB3261E),
)

private val AppTypography = Typography(
    headlineSmall = TextStyle(fontSize = 24.sp, lineHeight = 30.sp, fontWeight = FontWeight.Medium),
    titleLarge = TextStyle(fontSize = 19.sp, lineHeight = 26.sp, fontWeight = FontWeight.Medium),
    titleMedium = TextStyle(fontSize = 16.sp, lineHeight = 22.sp, fontWeight = FontWeight.Medium),
    titleSmall = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.Medium),
    bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 25.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 23.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 18.sp),
    labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 18.sp, fontWeight = FontWeight.Medium),
    labelMedium = TextStyle(fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium),
    labelSmall = TextStyle(fontSize = 11.sp, lineHeight = 14.sp, fontWeight = FontWeight.Medium),
)

@Composable
fun ZhisuiTheme(mode: ThemeMode, content: @Composable () -> Unit) {
    val dark = when (mode) {
        ThemeMode.DARK -> true
        ThemeMode.LIGHT -> false
        ThemeMode.SYSTEM -> isSystemInDarkTheme()
    }
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            val controller = WindowCompat.getInsetsController(window, view)
            controller.isAppearanceLightStatusBars = !dark
            controller.isAppearanceLightNavigationBars = !dark
        }
    }
    MaterialTheme(
        colorScheme = if (dark) DarkColors else LightColors,
        typography = AppTypography,
        content = content,
    )
}
