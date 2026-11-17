"""Whiskers LoRA SFT; reused training method, independent data/adapter/output paths."""
import argparse
import hashlib
import json
import math
import random
import time
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--work', required=True)
    ap.add_argument('--epochs', type=int, default=2)
    ap.add_argument('--batch', type=int, default=2)
    ap.add_argument('--accum', type=int, default=8)
    ap.add_argument('--ctx', type=int, default=2048)
    args = ap.parse_args()
    root = Path(args.work)
    if (root / 'adapter').exists() or (root / 'merged').exists():
        raise SystemExit('Choose a fresh work directory; existing model outputs are never overwritten')
    random.seed(20261117)
    torch.manual_seed(20261117)
    torch.cuda.manual_seed_all(20261117)
    torch.set_num_threads(8)
    torch.backends.cuda.matmul.allow_tf32 = True
    tok = AutoTokenizer.from_pretrained(args.base, local_files_only=True, trust_remote_code=False)
    tok.pad_token = tok.eos_token

    def encode(row):
        messages = row['messages']
        prefix = tok.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True, enable_thinking=False)
        x = tok(prefix, add_special_tokens=False)['input_ids']
        y = tok(messages[-1]['content'] + '<|im_end|>\n', add_special_tokens=False)['input_ids']
        return x + y, [-100] * len(x) + y

    data = {}
    stats = {}
    for split in ('train', 'validation'):
        rows = [json.loads(l) for l in (root / f'data/{split}.jsonl').read_text().splitlines()]
        enc = [encode(r) for r in rows]
        data[split] = [r for r in enc if len(r[0]) <= args.ctx]
        stats[split] = {'input_rows': len(rows), 'used': len(data[split]),
                        'filtered_long': len(rows) - len(data[split]),
                        'tokens': sum(len(x) for x, _ in data[split])}
        if not data[split]:
            raise RuntimeError(f'No usable {split} rows')

    model = AutoModelForCausalLM.from_pretrained(args.base, local_files_only=True, trust_remote_code=False,
                use_safetensors=True, dtype=torch.bfloat16, attn_implementation='sdpa').cuda()
    model.config.use_cache = False
    model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05,
                target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'],
                task_type='CAUSAL_LM', bias='none'))
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    model.enable_input_require_grads()
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    model.print_trainable_parameters()
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=2e-4, weight_decay=0.01)
    batch_count = math.ceil(len(data['train']) / args.batch)
    steps = math.ceil(batch_count / args.accum) * args.epochs
    scheduler = get_cosine_schedule_with_warmup(optimizer, max(3, steps // 20), steps)

    def collate(rows):
        n = max(len(x) for x, _ in rows)
        ids = torch.full((len(rows), n), tok.pad_token_id, dtype=torch.long)
        labels = torch.full_like(ids, -100)
        mask = torch.zeros_like(ids)
        for i, (x, y) in enumerate(rows):
            ids[i, :len(x)] = torch.tensor(x)
            labels[i, :len(y)] = torch.tensor(y)
            mask[i, :len(x)] = 1
        return {'input_ids': ids.cuda(), 'labels': labels.cuda(), 'attention_mask': mask.cuda()}

    @torch.no_grad()
    def val_loss():
        model.eval()
        weighted, tokens = 0., 0
        for start in range(0, len(data['validation']), args.batch):
            batch = collate(data['validation'][start:start + args.batch])
            count = int((batch['labels'][:, 1:] != -100).sum())
            weighted += model(**batch).loss.item() * count
            tokens += count
        model.train()
        return weighted / max(tokens, 1)

    start = time.time()
    baseline = val_loss()
    print(json.dumps({'event': 'start', 'stats': stats, 'trainable': trainable, 'steps': steps,
                      'validation_loss_before': baseline}), flush=True)
    step = 0
    log = []
    model.train()
    optimizer.zero_grad(set_to_none=True)
    for epoch in range(args.epochs):
        rows = sorted(data['train'], key=lambda r: len(r[0]))
        batches = [rows[i:i + args.batch] for i in range(0, len(rows), args.batch)]
        random.shuffle(batches)
        for group in range(0, len(batches), args.accum):
            chunk = batches[group:group + args.accum]
            loss_sum = 0.
            for rows in chunk:
                loss = model(**collate(rows)).loss
                loss_sum += loss.item()
                (loss / len(chunk)).backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad(set_to_none=True)
            step += 1
            entry = {'event': 'step', 'epoch': epoch + 1, 'step': step, 'loss': loss_sum / len(chunk),
                     'seconds': round(time.time() - start, 2),
                     'peak_gpu_mib': round(torch.cuda.max_memory_allocated() / 2**20)}
            log.append(entry)
            if step % 5 == 0 or step == 1:
                print(json.dumps(entry), flush=True)
        print(json.dumps({'event': 'epoch', 'epoch': epoch + 1, 'validation_loss': val_loss()}), flush=True)

    after = val_loss()
    (root / 'adapter').mkdir(exist_ok=True)
    model.save_pretrained(root / 'adapter')
    tok.save_pretrained(root / 'adapter')
    model = model.cpu()
    torch.cuda.empty_cache()
    merged = model.merge_and_unload()
    merged.config.use_cache = True
    merged.save_pretrained(root / 'merged', safe_serialization=True)
    tok.save_pretrained(root / 'merged')
    summary = {'project': 'whiskers-mini-ti', 'base': args.base, 'seed': 20261117, 'dtype': 'bfloat16', 'lora_rank': 16,
               'lora_alpha': 32, 'epochs': args.epochs, 'microbatch': args.batch,
               'accum': args.accum, 'context': args.ctx, 'trainable': trainable, 'steps': step,
               'seconds': round(time.time() - start, 2), 'stats': stats,
               'validation_loss_before': baseline, 'validation_loss_after': after,
               'peak_gpu_allocated_mib': round(torch.cuda.max_memory_allocated() / 2**20),
               'torch': torch.__version__, 'gpu': torch.cuda.get_device_name(0),
               'data_sha256': {s: hashlib.sha256((root / f'data/{s}.jsonl').read_bytes()).hexdigest()
                               for s in ('train', 'validation')},
               'note': 'Family/alias-heldout, template-derived selection tasks; not detection accuracy. No resume checkpoint.'}
    (root / 'training-summary.json').write_text(json.dumps(summary, indent=2))
    (root / 'training-steps.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in log))
    print(json.dumps({'event': 'done', **summary}), flush=True)


if __name__ == '__main__':
    main()
