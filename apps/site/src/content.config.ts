import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";

const blog = defineCollection({
  loader: glob({ pattern: "**/*.{md,mdx}", base: "./src/content/blog" }),
  schema: z.object({
    entry: z.string().optional(),
    title: z.string(),
    excerpt: z.string().optional(),
    description: z.string().optional(),
    date: z.coerce.date(),
    author: z.string().default("Julio Caesar"),
    categories: z.array(z.string()).optional(),
    meta: z
      .object({
        keywords: z.array(z.string()).optional(),
      })
      .optional(),
    bannerCloudinaryId: z.string().optional(),
    metaImage: z.string().optional(),
    featuredImage: z.string().optional(),
    bannerAlt: z.string().optional(),
    bannerTitle: z.string().optional(),
    bannerCredit: z.string().optional(),
    draft: z.boolean().optional().default(false),
  }),
});

const docs = defineCollection({
  loader: glob({ pattern: "**/*.{md,mdx}", base: "./src/content/docs" }),
  schema: z.object({
    title: z.string(),
    description: z.string().optional(),
    author: z.string().optional(),
  }),
});

export const collections = { blog, docs };
