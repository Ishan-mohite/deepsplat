package com.deepsplat.capture

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.rememberCoroutineScope
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.deepsplat.capture.ui.screens.CaptureScreen
import com.deepsplat.capture.ui.screens.HomeScreen
import com.deepsplat.capture.ui.screens.RecordScreen
import com.deepsplat.capture.ui.screens.SettingsScreen
import com.deepsplat.capture.ui.theme.DeepsplatTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.light(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.light(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT),
        )
        val graph = (application as DeepsplatApp).graph
        setContent { DeepsplatTheme { App(graph) } }
    }
}

@Composable
private fun App(graph: AppGraph) {
    val nav = rememberNavController()
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) { graph.uploads.followProcessing(scope) }
    NavHost(
        nav, startDestination = "home",
        enterTransition = { slideInHorizontally { it / 6 } + fadeIn() },
        exitTransition = { fadeOut() },
        popEnterTransition = { fadeIn() },
        popExitTransition = { slideOutHorizontally { it / 6 } + fadeOut() },
    ) {
        composable("home") {
            HomeScreen(graph, onRecord = { nav.navigate("record") }, onOpen = { nav.navigate("capture/$it") }, onSettings = { nav.navigate("settings") })
        }
        composable("record", enterTransition = { fadeIn() }, popExitTransition = { fadeOut() }) {
            RecordScreen(graph, onClose = { nav.popBackStack() }, onSaved = { id ->
                nav.navigate("capture/$id") { popUpTo("record") { inclusive = true } }
            })
        }
        composable("capture/{id}") { entry ->
            CaptureScreen(graph, entry.arguments?.getString("id").orEmpty(), onBack = { nav.popBackStack() })
        }
        composable("settings") {
            SettingsScreen(graph, onBack = { nav.popBackStack() })
        }
    }
}
