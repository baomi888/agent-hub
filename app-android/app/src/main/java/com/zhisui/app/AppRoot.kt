package com.zhisui.app

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.MailOutline
import androidx.compose.material.icons.filled.Person
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import com.zhisui.app.domain.model.ConversationId
import com.zhisui.app.ui.chat.ChatScreen
import com.zhisui.app.ui.chat.ConversationListScreen
import com.zhisui.app.ui.profile.ProfileScreen
import com.zhisui.app.ui.study.StudyScreen
import com.zhisui.app.ui.theme.ThemeMode
import com.zhisui.app.ui.theme.ZhisuiTheme

private data class HomeTab(val label: String, val icon: ImageVector)

private val homeTabs = listOf(
    HomeTab("对话", Icons.Filled.MailOutline),
    HomeTab("学习", Icons.Filled.DateRange),
    HomeTab("我的", Icons.Filled.Person),
)

@Composable
fun AppRoot(container: AppContainer) {
    var themeName by rememberSaveable { mutableStateOf(ThemeMode.DARK.name) }
    var tabIndex by rememberSaveable { mutableIntStateOf(0) }
    var openConversation by rememberSaveable { mutableStateOf<String?>(null) }

    val themeMode = runCatching { ThemeMode.valueOf(themeName) }.getOrDefault(ThemeMode.DARK)

    ZhisuiTheme(mode = themeMode) {
        Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            val current = openConversation
            if (current != null) {
                ChatScreen(
                    repository = container.conversationRepository,
                    conversationId = ConversationId(current),
                    onBack = { openConversation = null },
                )
            } else {
                Scaffold(
                    containerColor = MaterialTheme.colorScheme.background,
                    bottomBar = {
                        NavigationBar(containerColor = MaterialTheme.colorScheme.background) {
                            homeTabs.forEachIndexed { index, tab ->
                                val selected = index == tabIndex
                                NavigationBarItem(
                                    selected = selected,
                                    onClick = { tabIndex = index },
                                    icon = { Icon(tab.icon, contentDescription = tab.label) },
                                    label = { Text(tab.label, style = MaterialTheme.typography.labelSmall) },
                                    colors = NavigationBarItemDefaults.colors(
                                        selectedIconColor = MaterialTheme.colorScheme.primary,
                                        selectedTextColor = MaterialTheme.colorScheme.primary,
                                        indicatorColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.14f),
                                        unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                                        unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                                    ),
                                )
                            }
                        }
                    },
                ) { innerPadding ->
                    Box(Modifier.padding(innerPadding)) {
                        when (tabIndex) {
                            0 -> ConversationListScreen(
                                repository = container.conversationRepository,
                                onOpen = { openConversation = it.value },
                            )

                            1 -> StudyScreen(repository = container.studyRepository)

                            else -> ProfileScreen(
                                repository = container.profileRepository,
                                themeMode = themeMode,
                                onThemeModeChange = { themeName = it.name },
                            )
                        }
                    }
                }
            }
        }
    }
}
