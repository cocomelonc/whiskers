// jni bridge between the app (slm/Llm.kt) and llama.cpp: one model, one context, one sequence.
//
// the app always sends the WHOLE prompt (chatml text, already rendered). the bridge keeps the
// tokens that are in the kv cache and only decodes what differs, so the common system/source prefix
// with the tool list is computed once per session and every tool round-trip only pays for the
// new tool result. greedy sampling (plus a mild repeat penalty): tool calls must be deterministic json.
#include <jni.h>
#include <android/log.h>

#include <string>
#include <vector>

#include "llama.h"

#define TAG "whiskers-llm"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

namespace {

struct Session {
    llama_model *model = nullptr;
    llama_context *ctx = nullptr;
    const llama_vocab *vocab = nullptr;
    llama_sampler *smpl = nullptr;
    std::vector<llama_token> cached;  // tokens whose kv entries are valid, in order
};

std::string jstr(JNIEnv *env, jstring s) {
    const char *c = env->GetStringUTFChars(s, nullptr);
    std::string out(c);
    env->ReleaseStringUTFChars(s, c);
    return out;
}

std::vector<llama_token> tokenize(const llama_vocab *vocab, const std::string &text) {
    // parse_special: <|im_start|>, <tool_call> etc. in the rendered template are single tokens
    int n = -llama_tokenize(vocab, text.data(), (int32_t) text.size(), nullptr, 0, false, true);
    std::vector<llama_token> out(n);
    llama_tokenize(vocab, text.data(), (int32_t) text.size(), out.data(), n, false, true);
    return out;
}

// decode tokens [from, end) of `toks` in n_batch chunks, appending them to the cache
bool feed(Session *s, const std::vector<llama_token> &toks, size_t from) {
    const size_t step = llama_n_batch(s->ctx);
    for (size_t i = from; i < toks.size(); i += step) {
        int32_t n = (int32_t) std::min(step, toks.size() - i);
        llama_batch b = llama_batch_get_one(const_cast<llama_token *>(toks.data() + i), n);
        if (llama_decode(s->ctx, b) != 0) {
            LOGE("decode failed at %zu", i);
            return false;
        }
        s->cached.insert(s->cached.end(), toks.begin() + i, toks.begin() + i + n);
    }
    return true;
}

}  // namespace

extern "C" {

JNIEXPORT jlong JNICALL
Java_com_cocomelonc_whiskers_slm_Llm_nativeLoad(JNIEnv *env, jobject, jstring path, jint threads, jint nctx) {
    llama_backend_init();
    auto mp = llama_model_default_params();
    // mmap: the model file is paged in on demand instead of copied into the heap (default is a full copy)
    mp.load_mode = LLAMA_LOAD_MODE_MMAP;
    llama_model *model = llama_model_load_from_file(jstr(env, path).c_str(), mp);
    if (!model) {
        LOGE("model load failed");
        return 0;
    }
    auto cp = llama_context_default_params();
    cp.n_ctx = nctx;
    cp.n_batch = 512;
    cp.n_threads = threads;
    cp.n_threads_batch = threads;
    llama_context *ctx = llama_init_from_model(model, cp);
    if (!ctx) {
        llama_model_free(model);
        LOGE("context init failed");
        return 0;
    }
    auto *s = new Session();
    s->model = model;
    s->ctx = ctx;
    s->vocab = llama_model_get_vocab(model);
    s->smpl = llama_sampler_chain_init(llama_sampler_chain_default_params());
    // mild repeat penalty on the last 64 generated tokens: plain greedy can loop on unconstrained lists
    // ; 1.1 is low enough to leave tool-call json intact
    llama_sampler_chain_add(s->smpl, llama_sampler_init_penalties(llama_vocab_n_tokens(s->vocab), 64, 1.1f, 0.0f, 0.0f));
    llama_sampler_chain_add(s->smpl, llama_sampler_init_greedy());
    LOGI("loaded, ctx %d, threads %d", nctx, threads);
    return reinterpret_cast<jlong>(s);
}

// number of tokens a text takes (the app trims chat history with it)
JNIEXPORT jint JNICALL
Java_com_cocomelonc_whiskers_slm_Llm_nativeCount(JNIEnv *env, jobject, jlong h, jstring text) {
    auto *s = reinterpret_cast<Session *>(h);
    return (jint) tokenize(s->vocab, jstr(env, text)).size();
}

// prompt -> generated text (raw utf-8 bytes, special tokens rendered). onPiece(byte[]) streams
// each token's bytes; returning false from it stops generation. maxTokens caps the reply.
JNIEXPORT jbyteArray JNICALL
Java_com_cocomelonc_whiskers_slm_Llm_nativeGenerate(JNIEnv *env, jobject, jlong h, jstring prompt, jint maxTokens, jobject cb, jstring grammarText) {
    auto *s = reinterpret_cast<Session *>(h);
    jmethodID onPiece = env->GetMethodID(env->GetObjectClass(cb), "onPiece", "([B)Z");
    std::vector<llama_token> toks = tokenize(s->vocab, jstr(env, prompt));
    const uint32_t nctx = llama_n_ctx(s->ctx);
    if (toks.size() + maxTokens > nctx) {
        LOGE("prompt %zu + %d > ctx %u", toks.size(), maxTokens, nctx);
        return nullptr;
    }

    // reuse the longest common prefix with what is already in the kv cache
    size_t keep = 0;
    while (keep < s->cached.size() && keep < toks.size() && s->cached[keep] == toks[keep]) keep++;
    // at least one token must be decoded to get fresh logits
    if (keep == toks.size()) keep--;
    llama_memory_seq_rm(llama_get_memory(s->ctx), 0, (llama_pos) keep, -1);
    s->cached.resize(keep);
    LOGI("prompt %zu tokens, %zu cached, decoding %zu", toks.size(), keep, toks.size() - keep);
    if (!feed(s, toks, keep)) return nullptr;

    std::string out;
    char buf[256];
    llama_sampler * sampler = s->smpl;
    llama_sampler * constrained = nullptr;
    std::string grammar = jstr(env, grammarText);
    if (!grammar.empty()) {
        auto * g = llama_sampler_init_grammar(s->vocab, grammar.c_str(), "root");
        if (!g) return nullptr;
        constrained = llama_sampler_chain_init(llama_sampler_chain_default_params());
        llama_sampler_chain_add(constrained, g);
        llama_sampler_chain_add(constrained, llama_sampler_init_greedy());
        sampler = constrained;
    }
    llama_sampler_reset(sampler);
    for (int i = 0; i < maxTokens; i++) {
        llama_token t = llama_sampler_sample(sampler, s->ctx, -1);
        if (llama_vocab_is_eog(s->vocab, t)) break;
        int n = llama_token_to_piece(s->vocab, t, buf, sizeof(buf), 0, true);
        if (n < 0) n = 0;
        out.append(buf, n);
        jbyteArray piece = env->NewByteArray(n);
        env->SetByteArrayRegion(piece, 0, n, reinterpret_cast<const jbyte *>(buf));
        jboolean go = env->CallBooleanMethod(cb, onPiece, piece);
        env->DeleteLocalRef(piece);
        // the sampled token goes into the cache too, so the next prompt (same text + more) reuses it
        std::vector<llama_token> one{t};
        if (!feed(s, one, 0) || !go) break;
    }
    jbyteArray res = env->NewByteArray((jsize) out.size());
    if (constrained) llama_sampler_free(constrained);
    env->SetByteArrayRegion(res, 0, (jsize) out.size(), reinterpret_cast<const jbyte *>(out.data()));
    return res;
}

JNIEXPORT void JNICALL
Java_com_cocomelonc_whiskers_slm_Llm_nativeFree(JNIEnv *, jobject, jlong h) {
    auto *s = reinterpret_cast<Session *>(h);
    if (!s) return;
    llama_sampler_free(s->smpl);
    llama_free(s->ctx);
    llama_model_free(s->model);
    delete s;
}

}  // extern "C"
