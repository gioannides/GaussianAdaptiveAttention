import torch
import torch.nn as nn
import torch.nn.functional as F

class GaussianAdaptiveAttention(nn.Module):
    def __init__(self, norm_axis, num_gaussians, initial_c=2, eps=1e-8, learnable_weights=True, padding_value=None):
        super().__init__()
        self.norm_axis = norm_axis
        self.eps = eps
        self.num_gaussians = num_gaussians
        self.padding_value = padding_value
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # Learnable mean offsets for each Gaussian
        self.mean_offsets = nn.Parameter(torch.zeros(num_gaussians, dtype=torch.float))

        # Initialize the scale factor 'c' as a learnable parameter
        if isinstance(initial_c, torch.Tensor):
            if initial_c.shape[0] != num_gaussians:
                raise ValueError(f"Provided standard deviation values must have length {num_gaussians}")
            else:
                self.c = nn.Parameter(initial_c.float())
        else:
            self.c = nn.Parameter(torch.full((num_gaussians,), initial_c, dtype=torch.float))

        # Initialize weights
        if learnable_weights is True:
            self.weights = nn.Parameter(torch.ones(num_gaussians))
        elif isinstance(learnable_weights, torch.Tensor):
            if learnable_weights.shape[0] != num_gaussians:
                raise ValueError(f"Provided weights must have length {num_gaussians}")
            self.weights = learnable_weights
        else:
            raise TypeError("learnable_weights must be either True or a torch.Tensor of shape (num_gaussians,)")

    def forward(self, x):
        x = x.to(self.device)

        # Apply mask if padding value is provided
        if self.padding_value is not None:
            mask = x != self.padding_value
            x_masked = torch.where(mask, x, torch.zeros_like(x))
        else:
            x_masked = x

        # Data-derived mean and variance
        mean = x_masked.mean(dim=self.norm_axis, keepdim=True)
        var = x_masked.var(dim=self.norm_axis, keepdim=True) + self.eps

        # Normalize weights
        normalized_weights = F.softmax(self.weights, dim=0) if isinstance(self.weights, nn.Parameter) else self.weights

        # Mixture of Gaussians with learned mean offsets
        mixture = 0
        for i in range(self.num_gaussians):
            adjusted_mean = mean + self.mean_offsets[i]
            y_norm = (x - adjusted_mean) / torch.sqrt(var)
            gaussian = torch.exp(-(y_norm ** 2) / (2.0 * (self.c[i] ** 2)))
            mixture += normalized_weights[i] * gaussian

        # Apply transformation
        y_transform = mixture / mixture.sum(dim=self.norm_axis, keepdim=True).clamp(min=self.eps)
        return torch.where(mask, x * y_transform, x) if self.padding_value is not None else x * y_transform



class MultiHeadGaussianAdaptiveAttention(nn.Module):
    def __init__(self, norm_axis, num_heads, num_gaussians, initial_variance=2, learnable_weights=True, padding_value=None, eps=1e-8):
        super().__init__()
        self.num_heads = num_heads
        self.norm_axis = norm_axis
        self.num_gaussians = num_gaussians
        self.padding_value = padding_value
        self.eps = eps
        self.learnable_weights = learnable_weights
        self.initial_variance = initial_variance

        self.attention_heads = nn.ModuleList([GaussianAdaptiveAttention(norm_axis=norm_axis, num_gaussians=num_gaussians, initial_c=initial_variance, eps=eps, learnable_weights=learnable_weights, padding_value=padding_value) for _ in range(num_heads)])

    def forward(self, x):
        # Validate chunk size
        chunk_size = x.shape[self.norm_axis] // self.num_heads
        if chunk_size == 0:
            raise ValueError("Input tensor size along norm_axis must be larger than the number of heads.")

        # Process each chunk with corresponding attention head
        return torch.cat([head(x.narrow(self.norm_axis, i * chunk_size, chunk_size)) for i, head in enumerate(self.attention_heads)], dim=self.norm_axis)
