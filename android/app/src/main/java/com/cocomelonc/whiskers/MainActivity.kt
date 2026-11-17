package com.cocomelonc.whiskers

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import androidx.lifecycle.ViewModelProvider
import java.util.Locale

private val Bg = Color(0xFF0B1020)
private val Panel = Color(0xFF141E32)
private val Line = Color(0xFF28364C)
private val Ink = Color(0xFFE9EEF8)
private val Muted = Color(0xFF9AABC3)
private val Teal = Color(0xFF69DCD5)
private val Coral = Color(0xFFEE5B85)
private val BodyFont = FontFamily(Font(R.font.inter_regular), Font(R.font.inter_semibold, FontWeight.SemiBold))
private val TitleFont = FontFamily(Font(R.font.space_grotesk_bold, FontWeight.Bold))

class MainActivity : ComponentActivity() {
    private val vm by lazy { ViewModelProvider(this)[ChatViewModel::class.java] }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Teal, secondary = Coral, surface = Panel, background = Bg, onSurface = Ink),
                typography = Typography(bodyLarge = MaterialTheme.typography.bodyLarge.copy(fontFamily = BodyFont))) {
                Whiskers(vm)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Whiskers(vm: ChatViewModel) {
    val context = LocalContext.current
    var question by rememberSaveable { mutableStateOf("") }
    var sources by remember { mutableStateOf(false) }
    var settings by remember { mutableStateOf(false) }
    var exportText by remember { mutableStateOf("") }
    var notice by remember { mutableStateOf("") }
    val list = rememberLazyListState()
    val save = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        if (uri != null) {
            notice = runCatching { context.contentResolver.openOutputStream(uri)!!.use { it.write(exportText.toByteArray()) }; "Saved" }.getOrElse { "Could not save file" }
        }
    }
    val import = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> if (uri != null) vm.importModel(uri) }
    val send = { if (question.isNotBlank()) { vm.ask(question); question = "" } }
    LaunchedEffect(vm.messages.size, vm.busy, vm.error) {
        val count = list.layoutInfo.totalItemsCount
        if (count > 0) list.animateScrollToItem(count - 1)
    }

    Box(Modifier.fillMaxSize().background(Bg)) {
        Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding()) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
                Image(painterResource(R.drawable.ti_cat), "Whiskers TI kitten", Modifier.size(42.dp))
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text("whiskers", fontFamily = TitleFont, fontWeight = FontWeight.Bold, fontSize = 26.sp, color = Ink)
                    Text("YOUR POCKET TI ANALYST", color = Teal, fontSize = 8.sp, letterSpacing = 1.7.sp)
                }
                TextButton(onClick = { settings = true }) { Text("Model", color = Muted, fontSize = 12.sp) }
            }
            HorizontalDivider(color = Line.copy(alpha = .65f))
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                vm.cards.forEachIndexed { index, card ->
                    FilterChip(selected = index == vm.selected, onClick = { vm.select(index) }, enabled = !vm.busy,
                        label = { Text(card.name, fontSize = 11.sp) },
                        colors = FilterChipDefaults.filterChipColors(selectedContainerColor = Teal.copy(alpha = .14f), selectedLabelColor = Teal, labelColor = Muted),
                        border = BorderStroke(1.dp, if (index == vm.selected) Teal.copy(alpha = .5f) else Line))
                }
            }
            Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(5.dp).background(if (vm.ready) Teal else Coral, CircleShape))
                Spacer(Modifier.width(6.dp))
                Text(vm.status, color = Muted, fontSize = 10.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                TextButton(onClick = { sources = true }, contentPadding = PaddingValues(4.dp), modifier = Modifier.height(30.dp)) { Text("View evidence ↗", color = Teal, fontSize = 10.sp) }
            }

            LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, contentPadding = PaddingValues(horizontal = 18.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                if (vm.messages.isEmpty() && !vm.busy) {
                    item {
                        Row(Modifier.fillMaxWidth().background(Brush.linearGradient(listOf(Panel, Bg)), RoundedCornerShape(24.dp)).padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("Curious cat.\nClear evidence.", fontFamily = TitleFont, fontWeight = FontWeight.Bold, fontSize = 25.sp, lineHeight = 29.sp, color = Ink)
                                Spacer(Modifier.height(10.dp))
                                Text("A small, local model.\nReal threat intelligence.", color = Muted, fontSize = 11.sp, lineHeight = 17.sp)
                            }
                            Image(painterResource(R.drawable.ti_cat), "TI analyst cat with a magnifying glass", Modifier.size(139.dp))
                        }
                    }
                    item {
                        Column(Modifier.fillMaxWidth().background(Panel, RoundedCornerShape(18.dp)).border(1.dp, Line, RoundedCornerShape(18.dp)).padding(17.dp)) {
                            Text("LET’S LOOK AT ${vm.card.name.uppercase(Locale.US)}", color = Teal, fontSize = 9.sp, letterSpacing = 1.3.sp)
                            Spacer(Modifier.height(10.dp))
                            Text("Ask a question. I’ll find the supporting passage — or leave it unknown.", color = Ink, fontSize = 14.sp, lineHeight = 21.sp)
                            Spacer(Modifier.height(13.dp))
                            Text("Malpedia · public record · updated ${vm.card.updated}", color = Muted, fontSize = 9.sp)
                        }
                    }
                    item {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Starter("Give me a brief.", vm.ready && !vm.busy) { vm.ask(it) }
                            Starter("Which actors are listed in this record?", vm.ready && !vm.busy) { vm.ask(it) }
                            Starter("What is its current C2 address?", vm.ready && !vm.busy) { vm.ask(it) }
                        }
                    }
                }
                items(vm.messages) { message ->
                    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                            Text(message.question, Modifier.widthIn(max = 310.dp).background(Coral.copy(alpha = .13f), RoundedCornerShape(18.dp,18.dp,4.dp,18.dp)).padding(14.dp), color = Ink, fontSize = 14.sp, lineHeight = 21.sp, fontFamily = BodyFont)
                        }
                        Column(Modifier.fillMaxWidth().background(Panel, RoundedCornerShape(4.dp,18.dp,18.dp,18.dp)).border(1.dp, Line, RoundedCornerShape(4.dp,18.dp,18.dp,18.dp)).padding(16.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Image(painterResource(R.drawable.ti_cat), null, Modifier.size(26.dp))
                                Spacer(Modifier.width(7.dp))
                                Text(if (message.answer.abstained) "UNKNOWN IN THIS SOURCE" else "WHISKERS · SOURCE EVIDENCE", fontSize = 9.sp, letterSpacing = 1.sp, color = if (message.answer.abstained) Color(0xFFFFCE83) else Teal)
                            }
                            Spacer(Modifier.height(11.dp))
                            Text(message.answer.text, color = Ink, fontSize = 13.sp, lineHeight = 21.sp, fontFamily = BodyFont)
                            Spacer(Modifier.height(12.dp))
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Text(if (message.origin == "source_policy") "source policy · no model inference" else String.format(Locale.US,"%.1f s · on device",message.seconds), color = Muted, fontSize = 9.sp, modifier = Modifier.weight(1f))
                                TextButton(onClick = { exportText = message.export; save.launch("whiskers-${vm.card.id}-evidence.json") }, contentPadding = PaddingValues(0.dp), modifier = Modifier.height(28.dp)) { Text("Save evidence ↓", color = Teal, fontSize = 10.sp) }
                            }
                            if (message.answer.passages.isNotEmpty()) Text(message.answer.passages.joinToString(" · ") { it.id }, color = Muted, fontSize = 9.sp, modifier = Modifier.clickable { sources = true })
                        }
                    }
                }
                if (vm.busy) item {
                    Column(Modifier.fillMaxWidth().background(Panel,RoundedCornerShape(18.dp)).padding(16.dp)) {
                        if (vm.pending.isNotBlank()) Text(vm.pending, color = Ink, fontSize = 13.sp, modifier = Modifier.padding(bottom = 10.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            CircularProgressIndicator(Modifier.size(15.dp), color = Teal, strokeWidth = 2.dp)
                            Text("  Reading the supplied evidence…", color = Muted, fontSize = 12.sp)
                        }
                    }
                }
                if (vm.error.isNotBlank()) item { Text(vm.error, color = Color(0xFFFFACB9), fontSize = 12.sp, modifier = Modifier.padding(8.dp)) }
                if (notice.isNotBlank()) item { Text(notice, color = Muted, fontSize = 11.sp) }
            }

            HorizontalDivider(color = Line)
            Row(Modifier.fillMaxWidth().background(Bg).padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(9.dp)) {
                OutlinedTextField(value = question, onValueChange = { question = it.take(600) }, enabled = vm.ready && !vm.busy, maxLines = 3,
                    modifier = Modifier.weight(1f), shape = RoundedCornerShape(20.dp),
                    placeholder = { Text("Ask about ${vm.card.name}…", fontSize = 12.sp) },
                    textStyle = LocalTextStyle.current.copy(fontFamily = BodyFont, fontSize = 13.sp, color = Ink),
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { send() }),
                    colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = Teal, unfocusedBorderColor = Line, disabledBorderColor = Line, cursorColor = Teal))
                Button(onClick = { if (vm.busy) vm.cancel() else send() }, enabled = vm.ready && (vm.busy || question.isNotBlank()),
                    shape = CircleShape, contentPadding = PaddingValues(0.dp), modifier = Modifier.size(53.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Coral, contentColor = Color.White)) {
                    Text(if (vm.busy) "■" else "↑", fontSize = if (vm.busy) 18.sp else 28.sp)
                }
            }
        }
    }

    if (sources) ModalBottomSheet(onDismissRequest = { sources = false }, containerColor = Bg) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 22.dp).padding(bottom = 32.dp), verticalArrangement = Arrangement.spacedBy(13.dp)) {
            Text(vm.card.name, fontFamily = TitleFont, fontSize = 25.sp, color = Ink)
            Text("PUBLIC MALPEDIA EVIDENCE", fontSize = 10.sp, color = Teal, letterSpacing = 1.sp)
            TextButton(onClick = { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(vm.card.source))) }, contentPadding = PaddingValues(0.dp)) { Text("Open original record ↗", color = Teal) }
            vm.card.passages.forEach { passage ->
                Column(Modifier.fillMaxWidth().background(Panel,RoundedCornerShape(12.dp)).padding(13.dp)) {
                    Text(passage.id, color = Teal, fontSize = 10.sp)
                    Text(passage.text, color = Ink, fontSize = 12.sp, lineHeight = 19.sp, modifier = Modifier.padding(top = 6.dp))
                }
            }
            for (i in 0 until vm.card.rules.length()) {
                val rule = vm.card.rules.getJSONObject(i)
                OutlinedButton(onClick = { exportText = rule.getString("rule"); save.launch(rule.getString("file")) }) { Text("Save original YARA ↓", color = Teal) }
            }
            Text("Source text is preserved. A historical record is not a live incident observation.", color = Muted, fontSize = 11.sp, lineHeight = 17.sp)
        }
    }

    if (settings) AlertDialog(onDismissRequest = { settings = false }, containerColor = Panel,
        title = { Text("Local model", fontFamily = TitleFont, color = Ink) },
        text = { Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(vm.status, color = Teal)
            Text("Whiskers mini TI · Qwen3-0.6B + LoRA\nEnglish · source evidence · runs on this device", color = Ink, fontSize = 12.sp, lineHeight = 19.sp)
            Text("SHA-256\n${vm.modelHash}", color = Muted, fontSize = 10.sp)
            TextButton(onClick = { settings = false; import.launch(arrayOf("*/*")) }, enabled = !vm.busy) { Text("Import matching Whiskers GGUF", color = Teal, fontSize = 12.sp) }
            TextButton(onClick = { vm.clear(); settings = false }, enabled = !vm.busy) { Text("Clear this chat", color = Muted) }
        } }, confirmButton = { TextButton(onClick = { settings = false }) { Text("Done", color = Teal) } })
}

@Composable
private fun Starter(text: String, enabled: Boolean, ask: (String) -> Unit) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(13.dp)).background(Panel.copy(alpha = .7f)).border(1.dp,Line,RoundedCornerShape(13.dp)).clickable(enabled = enabled) { ask(text) }.padding(horizontal = 14.dp, vertical = 13.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(text, color = if (enabled) Muted else Muted.copy(alpha = .45f), fontSize = 11.sp, modifier = Modifier.weight(1f))
        Text("↗", color = Teal, fontSize = 15.sp)
    }
}
