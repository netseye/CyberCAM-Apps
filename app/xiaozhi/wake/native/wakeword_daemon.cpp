// Persistent sherpa-onnx keyword spotter for the CyberCAM K230.
//
// SIGUSR2 starts microphone capture, SIGUSR1 pauses it, and SIGTERM exits.
// The expensive ONNX model remains loaded while capture is paused.

#include <alsa/asoundlib.h>
#include <signal.h>
#include <stdint.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

extern "C" {

typedef struct {
  const char *encoder;
  const char *decoder;
  const char *joiner;
} SherpaOnnxOnlineTransducerModelConfig;
typedef struct { const char *encoder; const char *decoder; } SherpaOnnxOnlineParaformerModelConfig;
typedef struct { const char *model; } SherpaOnnxOnlineZipformer2CtcModelConfig;
typedef struct { const char *model; } SherpaOnnxOnlineNemoCtcModelConfig;
typedef struct { const char *model; } SherpaOnnxOnlineToneCtcModelConfig;

typedef struct {
  SherpaOnnxOnlineTransducerModelConfig transducer;
  SherpaOnnxOnlineParaformerModelConfig paraformer;
  SherpaOnnxOnlineZipformer2CtcModelConfig zipformer2_ctc;
  const char *tokens;
  int32_t num_threads;
  const char *provider;
  int32_t debug;
  const char *model_type;
  const char *modeling_unit;
  const char *bpe_vocab;
  const char *tokens_buf;
  int32_t tokens_buf_size;
  SherpaOnnxOnlineNemoCtcModelConfig nemo_ctc;
  SherpaOnnxOnlineToneCtcModelConfig t_one_ctc;
} SherpaOnnxOnlineModelConfig;

typedef struct { int32_t sample_rate; int32_t feature_dim; } SherpaOnnxFeatureConfig;
typedef struct {
  SherpaOnnxFeatureConfig feat_config;
  SherpaOnnxOnlineModelConfig model_config;
  int32_t max_active_paths;
  int32_t num_trailing_blanks;
  float keywords_score;
  float keywords_threshold;
  const char *keywords_file;
  const char *keywords_buf;
  int32_t keywords_buf_size;
} SherpaOnnxKeywordSpotterConfig;

typedef struct SherpaOnnxKeywordSpotter SherpaOnnxKeywordSpotter;
typedef struct SherpaOnnxOnlineStream SherpaOnnxOnlineStream;
typedef struct {
  const char *keyword;
  const char *tokens;
  const char *const *tokens_arr;
  int32_t count;
  float *timestamps;
  float start_time;
  const char *json;
} SherpaOnnxKeywordResult;

const SherpaOnnxKeywordSpotter *SherpaOnnxCreateKeywordSpotter(
    const SherpaOnnxKeywordSpotterConfig *config);
void SherpaOnnxDestroyKeywordSpotter(const SherpaOnnxKeywordSpotter *spotter);
const SherpaOnnxOnlineStream *SherpaOnnxCreateKeywordStream(
    const SherpaOnnxKeywordSpotter *spotter);
void SherpaOnnxDestroyOnlineStream(const SherpaOnnxOnlineStream *stream);
void SherpaOnnxOnlineStreamAcceptWaveform(const SherpaOnnxOnlineStream *stream,
                                          int32_t sample_rate,
                                          const float *samples, int32_t n);
int32_t SherpaOnnxIsKeywordStreamReady(const SherpaOnnxKeywordSpotter *spotter,
                                       const SherpaOnnxOnlineStream *stream);
void SherpaOnnxDecodeKeywordStream(const SherpaOnnxKeywordSpotter *spotter,
                                   const SherpaOnnxOnlineStream *stream);
const SherpaOnnxKeywordResult *SherpaOnnxGetKeywordResult(
    const SherpaOnnxKeywordSpotter *spotter,
    const SherpaOnnxOnlineStream *stream);
void SherpaOnnxDestroyKeywordResult(const SherpaOnnxKeywordResult *result);
}

namespace {
volatile sig_atomic_t g_start_requested = 0;
volatile sig_atomic_t g_stop_requested = 0;
volatile sig_atomic_t g_exit_requested = 0;

void HandleSignal(int signal_number) {
  if (signal_number == SIGUSR2) {
    g_start_requested = 1;
    g_stop_requested = 0;
  } else if (signal_number == SIGUSR1) {
    g_start_requested = 0;
    g_stop_requested = 1;
  } else {
    g_exit_requested = 1;
    g_stop_requested = 1;
  }
}

void Print(const std::string &line) {
  std::cout << line << std::endl;
}

snd_pcm_t *OpenCapture(const char *device) {
  snd_pcm_t *pcm = nullptr;
  int status = snd_pcm_open(&pcm, device, SND_PCM_STREAM_CAPTURE, SND_PCM_NONBLOCK);
  if (status < 0) return nullptr;
  status = snd_pcm_set_params(pcm, SND_PCM_FORMAT_S16_LE,
                              SND_PCM_ACCESS_RW_INTERLEAVED, 1, 16000, 1, 500000);
  if (status < 0) {
    snd_pcm_close(pcm);
    return nullptr;
  }
  return pcm;
}

class AudioRing {
 public:
  explicit AudioRing(size_t capacity) : capacity_(capacity) {}

  void Push(std::vector<int16_t> block) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (blocks_.size() == capacity_) {
      blocks_.pop_front();
      ++dropped_blocks_;
    }
    blocks_.push_back(std::move(block));
    ready_.notify_one();
  }

  bool Pop(std::vector<int16_t> *block, int timeout_ms) {
    std::unique_lock<std::mutex> lock(mutex_);
    if (!ready_.wait_for(lock, std::chrono::milliseconds(timeout_ms),
                         [this] { return !blocks_.empty(); })) {
      return false;
    }
    *block = std::move(blocks_.front());
    blocks_.pop_front();
    return true;
  }

  void Clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    blocks_.clear();
    dropped_blocks_ = 0;
  }

  uint64_t TakeDroppedBlocks() {
    std::lock_guard<std::mutex> lock(mutex_);
    uint64_t value = dropped_blocks_;
    dropped_blocks_ = 0;
    return value;
  }

 private:
  const size_t capacity_;
  std::mutex mutex_;
  std::condition_variable ready_;
  std::deque<std::vector<int16_t>> blocks_;
  uint64_t dropped_blocks_ = 0;
};

void CaptureLoop(snd_pcm_t *pcm, AudioRing *ring,
                 std::atomic<bool> *capture_running,
                 std::atomic<uint64_t> *alsa_overruns) {
  std::vector<int16_t> buffer(1600);
  while (capture_running->load(std::memory_order_relaxed)) {
    snd_pcm_sframes_t count = snd_pcm_readi(pcm, buffer.data(), buffer.size());
    if (count == -EAGAIN || count == -EINTR) {
      usleep(3000);
      continue;
    }
    if (count == -EPIPE) {
      ++(*alsa_overruns);
      snd_pcm_prepare(pcm);
      continue;
    }
    if (count < 0) {
      capture_running->store(false, std::memory_order_relaxed);
      break;
    }
    ring->Push(std::vector<int16_t>(buffer.begin(), buffer.begin() + count));
  }
}
}  // namespace

int main(int argc, char **argv) {
  if (argc != 9) {
    std::cerr << "usage: wakeword-daemon tokens encoder decoder joiner keywords "
                 "device score threshold\n";
    return 2;
  }
  signal(SIGUSR1, HandleSignal);
  signal(SIGUSR2, HandleSignal);
  signal(SIGTERM, HandleSignal);
  signal(SIGINT, HandleSignal);

  SherpaOnnxKeywordSpotterConfig config;
  std::memset(&config, 0, sizeof(config));
  config.feat_config.sample_rate = 16000;
  config.feat_config.feature_dim = 80;
  config.model_config.transducer.encoder = argv[2];
  config.model_config.transducer.decoder = argv[3];
  config.model_config.transducer.joiner = argv[4];
  config.model_config.tokens = argv[1];
  config.model_config.num_threads = 1;
  config.model_config.provider = "cpu";
  config.model_config.model_type = "zipformer2";
  config.model_config.modeling_unit = "cjkchar";
  config.max_active_paths = 4;
  config.num_trailing_blanks = 1;
  config.keywords_score = std::stof(argv[7]);
  config.keywords_threshold = std::stof(argv[8]);
  config.keywords_file = argv[5];

  const SherpaOnnxKeywordSpotter *spotter =
      SherpaOnnxCreateKeywordSpotter(&config);
  if (!spotter) {
    Print("ERROR\t无法加载唤醒模型");
    return 3;
  }
  Print("MODEL_READY");

  snd_pcm_t *pcm = nullptr;
  const SherpaOnnxOnlineStream *stream = nullptr;
  AudioRing audio_ring(20);  // Two seconds of 100 ms capture blocks.
  std::thread capture_thread;
  std::atomic<bool> capture_running(false);
  std::atomic<uint64_t> alsa_overruns(0);
  std::vector<int16_t> pcm_samples;
  std::vector<float> samples;
  float previous_input = 0.0f;
  float previous_output = 0.0f;

  auto stop_capture = [&]() {
    capture_running.store(false, std::memory_order_relaxed);
    if (capture_thread.joinable()) capture_thread.join();
    if (pcm) {
      snd_pcm_drop(pcm);
      snd_pcm_close(pcm);
      pcm = nullptr;
    }
    audio_ring.Clear();
    if (stream) {
      SherpaOnnxDestroyOnlineStream(stream);
      stream = nullptr;
    }
    previous_input = previous_output = 0.0f;
  };

  while (!g_exit_requested) {
    if (g_stop_requested) {
      stop_capture();
      g_stop_requested = 0;
      Print("PAUSED");
    }
    if (!pcm && g_start_requested) {
      stream = SherpaOnnxCreateKeywordStream(spotter);
      pcm = OpenCapture(argv[6]);
      if (!stream || !pcm) {
        stop_capture();
        g_start_requested = 0;
        Print("ERROR\t麦克风暂时不可用");
      } else {
        g_start_requested = 0;
        capture_running.store(true, std::memory_order_relaxed);
        capture_thread = std::thread(CaptureLoop, pcm, &audio_ring,
                                     &capture_running, &alsa_overruns);
        Print("LISTENING");
      }
    }
    if (!pcm) {
      usleep(10000);
      continue;
    }

    if (!capture_running.load(std::memory_order_relaxed)) {
      stop_capture();
      Print("ERROR\t麦克风读取失败");
      continue;
    }
    if (!audio_ring.Pop(&pcm_samples, 20)) continue;
    uint64_t overruns = alsa_overruns.exchange(0);
    uint64_t dropped = audio_ring.TakeDroppedBlocks();
    if (overruns || dropped) {
      std::cerr << "[wake] capture backlog: alsa_overruns=" << overruns
                << " dropped_blocks=" << dropped << std::endl;
    }
    samples.resize(pcm_samples.size());
    for (size_t i = 0; i < pcm_samples.size(); ++i) {
      float input = static_cast<float>(pcm_samples[i]);
      float output = input - previous_input + 0.98f * previous_output;
      previous_input = input;
      previous_output = output;
      samples[i] = std::max(-1.0f, std::min(1.0f, output / 32768.0f));
    }
    SherpaOnnxOnlineStreamAcceptWaveform(
        stream, 16000, samples.data(), static_cast<int32_t>(samples.size()));
    while (SherpaOnnxIsKeywordStreamReady(spotter, stream)) {
      SherpaOnnxDecodeKeywordStream(spotter, stream);
    }
    const SherpaOnnxKeywordResult *result =
        SherpaOnnxGetKeywordResult(spotter, stream);
    if (result && result->keyword && result->keyword[0]) {
      std::string keyword(result->keyword);
      SherpaOnnxDestroyKeywordResult(result);
      stop_capture();
      Print("DETECTED\t" + keyword);
      continue;
    }
    if (result) SherpaOnnxDestroyKeywordResult(result);
  }

  stop_capture();
  SherpaOnnxDestroyKeywordSpotter(spotter);
  Print("EXITING");
  return 0;
}
