/* Native-only substitutes for BPF helper addresses. No kernel calls. */
#define SEC(name)
#define __uint(name, value) int (*name)[value]
#define __type(name, value) typeof(value) *name
#ifndef __always_inline
#define __always_inline inline __attribute__((always_inline))
#endif
static void *bpf_map_lookup_elem(const void *, const void *);
static long bpf_redirect(unsigned int, unsigned long long);
static unsigned long long bpf_ktime_get_ns(void);
static long bpf_ringbuf_output(const void *, void *, unsigned long long, unsigned long long);
